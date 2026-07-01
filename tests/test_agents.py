"""M2 unit tests ($0): persona registry, Project Profile loader, providers, Agent Runner.

The runner is exercised against a fake ModelProvider, and each provider against a fake
backend client — no network, no tokens. Covers the M2 `CON` (output-contract) checks, the
provider abstraction, and the runner's bounded-re-ask path.
"""

from types import SimpleNamespace

import pytest

from orchestrator.agents.provider import ProviderResponse
from orchestrator.agents.providers.anthropic_provider import AnthropicProvider
from orchestrator.agents.providers.factory import build_provider
from orchestrator.agents.providers.vercel_provider import VercelGatewayProvider
from orchestrator.agents.registry import get_persona
from orchestrator.agents.registry.contracts import TriageOutput
from orchestrator.agents.runner import AgentRunner
from orchestrator.projects.loader import load_profile
from orchestrator.projects.profile import (
    Deploy,
    DeployKind,
    Intake,
    IntakeKind,
    ProjectProfile,
    Repo,
    Stack,
)
from orchestrator.shared.errors import NonRetryableAgentError

PROFILE = load_profile("meal-planner")


# --- registry ------------------------------------------------------------------
def test_registry_resolves_and_rejects_unknown():
    assert get_persona("triage").tier == "haiku"
    assert get_persona("pm_draft_brief").tier == "opus"
    with pytest.raises(KeyError):
        get_persona("nope")


def test_council_personas_on_sonnet_tier():
    # Council votes are the first Sonnet swap; fail loudly if they drift off-tier.
    assert get_persona("council_legal").tier == "sonnet"
    assert get_persona("council_sales").tier == "sonnet"


def test_architect_personas_on_opus_tier():
    # The architect reviews/plans on Opus; fail loudly if they drift down a tier.
    assert get_persona("architect_review_prd").tier == "opus"
    assert get_persona("architect_plan_stories").tier == "opus"


def test_story_plan_complexity_bounds_count():
    """The scope signal: the story-count ceiling per complexity is enforced at parse time so
    an over-decomposed 'small' feature is rejected (→ runner re-asks), not silently shipped."""
    import pydantic

    from orchestrator.agents.registry.contracts import PlannedStory, StoryPlanOutput

    def _stories(n: int) -> list[PlannedStory]:
        return [PlannedStory(title=f"slice {i}", estimate=1) for i in range(n)]

    # A small feature is capped at 3 stories — 3 is fine, 4 is rejected.
    assert len(StoryPlanOutput(complexity="small", stories=_stories(3)).stories) == 3
    with pytest.raises(pydantic.ValidationError):
        StoryPlanOutput(complexity="small", stories=_stories(4))
    # Larger complexities allow more; an empty plan is always rejected.
    assert len(StoryPlanOutput(complexity="large", stories=_stories(10)).stories) == 10
    with pytest.raises(pydantic.ValidationError):
        StoryPlanOutput(complexity="medium", stories=_stories(0))


def test_tier_downgrades_opus_to_sonnet_only_for_small():
    """The cost lever (§10): small features drop the Opus reasoning stages to Sonnet; anything
    non-trivial (or unknown complexity) keeps Opus, and non-Opus tiers are never touched."""
    from orchestrator.activities.agent_backed import _tier_for

    assert _tier_for("opus", "small") == "sonnet"
    assert _tier_for("opus", "medium") == "opus"
    assert _tier_for("opus", "large") == "opus"
    assert _tier_for("opus", "") == "opus"        # unknown → keep Opus (safe default)
    assert _tier_for("sonnet", "small") == "sonnet"  # non-Opus tiers unaffected


def test_prd_authoring_uses_sonnet_for_small_features():
    """A small-complexity brief routes PRD authoring to Sonnet (and threads complexity onto the
    PRD so the architect stages reuse the lever); a medium brief stays on Opus."""
    from orchestrator.activities.agent_backed import author_prd_with_runner
    from orchestrator.agents.registry.contracts import PRDAuthoringOutput
    from orchestrator.shared.types import Brief

    out = PRDAuthoringOutput(content="# PRD\n...", acceptance_criteria=["does X"], open_issues=[])

    def _brief(complexity: str) -> Brief:
        return Brief(summary="Add a toggle", problem="p", target_users="u",
                     ui_impacting=True, complexity=complexity, project="meal-planner")

    small_provider = _FakeProvider(out, 1000, 400, model_id="claude-sonnet-5")
    prd = author_prd_with_runner(small_provider, _brief("small"))
    assert small_provider.calls[0]["tier"] == "sonnet"
    assert prd.complexity == "small"              # carried for the architect stages

    medium_provider = _FakeProvider(out, 1000, 400, model_id="claude-opus-4-8")
    author_prd_with_runner(medium_provider, _brief("medium"))
    assert medium_provider.calls[0]["tier"] == "opus"


# --- project profile -----------------------------------------------------------
def test_profile_loads_and_validates():
    assert PROFILE.id == "meal-planner"
    assert PROFILE.stack.languages == ["typescript"]
    assert PROFILE.intake.kind is IntakeKind.DB_TABLE


def test_profile_loader_rejects_unknown():
    with pytest.raises(KeyError):
        load_profile("does-not-exist")


def test_profile_validation_rejects_inline_secret():
    # Assemble the key marker at runtime so the literal never appears in source (the R4
    # secret scan would otherwise flag this file).
    inline_value = "sk-" + "ant-" + "deadbeefcafe1234"
    bad = ProjectProfile(
        id="x", name="X", description="d",
        repo=Repo("git@x", "main"),
        stack=Stack(["py"], "pip", "pytest"),
        intake=Intake(IntakeKind.MANUAL),
        deploy=Deploy(DeployKind.MERGE),
        secret_refs={"key": inline_value},  # a value, not an env-var name
    )
    with pytest.raises(ValueError):
        bad.validate()


# --- agent runner (provider-agnostic) -----------------------------------------
class _FakeProvider:
    name = "fake"

    def __init__(self, payload, in_tok, out_tok, model_id="claude-haiku-4-5", cache=0):
        self._resp = ProviderResponse(payload, model_id, in_tok, out_tok, cache)
        self.calls = []

    def generate_structured(self, **kwargs):
        self.calls.append(kwargs)
        return self._resp


def test_runner_returns_validated_payload_and_dollar_cost():
    parsed = TriageOutput(kind="bug", priority="P1", needs_clarification=False, rationale="ok")
    provider = _FakeProvider(parsed, 1000, 200)  # haiku $1/$5 per 1M
    result = AgentRunner(provider).run(get_persona("triage"), PROFILE, "app crashes")

    assert isinstance(result.payload, TriageOutput)
    assert result.model == "claude-haiku-4-5"
    assert result.cost_usd == pytest.approx(0.002)  # 1000×$1/1e6 + 200×$5/1e6
    # Project context injected into the system prompt; correct tier passed through.
    assert provider.calls[0]["tier"] == "haiku"
    assert "Meal Planner" in provider.calls[0]["system"]


def test_runner_bounded_reask_then_hard_fail():
    provider = _FakeProvider(payload=None, in_tok=100, out_tok=10)  # never parses
    with pytest.raises(NonRetryableAgentError):
        AgentRunner(provider).run(get_persona("triage"), PROFILE, "x")
    assert len(provider.calls) == 2  # triage max_reask=1 -> 2 attempts


# --- anthropic provider --------------------------------------------------------
def _usage(inp, out, cache=0):
    return SimpleNamespace(input_tokens=inp, output_tokens=out, cache_read_input_tokens=cache)


def test_anthropic_provider_sets_thinking_effort_for_reasoning_tiers_only():
    parsed = TriageOutput(kind="feature", priority="P2", needs_clarification=False, rationale="r")

    class FakeMessages:
        def __init__(self):
            self.calls = []

        def parse(self, **kw):
            self.calls.append(kw)
            return SimpleNamespace(parsed_output=parsed, usage=_usage(10, 5))

    fm = FakeMessages()
    provider = AnthropicProvider(messages_client=fm)
    msgs = [{"role": "user", "content": "x"}]

    provider.generate_structured(tier="opus", system="s", messages=msgs,
                                 output_model=TriageOutput, effort="high", max_tokens=100)
    assert fm.calls[-1]["model"] == "claude-opus-4-8"
    assert fm.calls[-1]["thinking"] == {"type": "adaptive"}
    assert fm.calls[-1]["output_config"]["effort"] == "high"

    provider.generate_structured(tier="haiku", system="s", messages=msgs,
                                 output_model=TriageOutput, effort="low", max_tokens=100)
    assert fm.calls[-1]["model"] == "claude-haiku-4-5"
    assert "thinking" not in fm.calls[-1]
    assert "output_config" not in fm.calls[-1]


# --- vercel gateway provider ---------------------------------------------------
def test_vercel_provider_builds_openai_request_and_validates_content():
    payload_json = TriageOutput(
        kind="bug", priority="P0", needs_clarification=False, rationale="r"
    ).model_dump_json()

    class FakeCompletions:
        def __init__(self):
            self.calls = []

        def create(self, **kw):
            self.calls.append(kw)
            message = SimpleNamespace(content=payload_json)
            usage = SimpleNamespace(prompt_tokens=120, completion_tokens=30)
            return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)

    fake = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    provider = VercelGatewayProvider(client=fake)
    resp = provider.generate_structured(
        tier="haiku", system="s", messages=[{"role": "user", "content": "x"}],
        output_model=TriageOutput, effort="low", max_tokens=100,
    )

    assert isinstance(resp.payload, TriageOutput) and resp.payload.kind == "bug"
    assert resp.model_id == "anthropic/claude-haiku-4.5"  # gateway-namespaced (dot versioning)
    assert resp.input_tokens == 120 and resp.output_tokens == 30
    call = fake.chat.completions.calls[0]
    assert call["model"] == "anthropic/claude-haiku-4.5"
    assert call["response_format"]["type"] == "json_schema"
    assert call["messages"][0]["role"] == "system"  # system folded into messages


# --- provider factory ----------------------------------------------------------
def test_factory_selects_provider_and_rejects_unknown():
    assert build_provider("anthropic").name == "anthropic"
    assert build_provider("vercel").name == "vercel"
    with pytest.raises(ValueError):
        build_provider("nope")


# --- runner-backed triage activity (M3 swap target) ----------------------------
def test_triage_activity_adapts_contract_to_workflow_type():
    from orchestrator.activities.agent_backed import triage_with_runner
    from orchestrator.shared.types import FeedbackKind, Triage

    parsed = TriageOutput(kind="feature", priority="P2", needs_clarification=False, rationale="r")
    provider = _FakeProvider(parsed, 1000, 200)  # haiku
    event = SimpleNamespace(
        id="x", kind=FeedbackKind.FEATURE, title="add button",
        body="please", submitted_by="t", project="meal-planner",
    )
    result = triage_with_runner(provider, event)

    assert isinstance(result, Triage)
    assert result.kind is FeedbackKind.FEATURE
    assert result.priority == "P2"
    assert result.cost_usd == pytest.approx(0.002)  # real dollar cost flows through


# --- runner-backed brief-authoring activity (M3 Opus swap) ---------------------
def test_draft_brief_activity_adapts_contract_and_carries_project():
    from orchestrator.activities.agent_backed import draft_brief_with_runner
    from orchestrator.agents.registry.contracts import BriefOutput
    from orchestrator.shared.types import Brief, FeedbackKind

    parsed = BriefOutput(summary="Dark mode toggle", problem="app too bright at night",
                         target_users="all users", ui_impacting=True, complexity="medium")
    provider = _FakeProvider(parsed, 1500, 500, model_id="claude-opus-4-8")  # opus $5/$25
    event = SimpleNamespace(id="x", kind=FeedbackKind.FEATURE, title="Dark mode",
                            body="too bright at night", submitted_by="t", project="meal-planner")

    brief = draft_brief_with_runner(provider, event)

    assert isinstance(brief, Brief)
    assert brief.summary == "Dark mode toggle"
    assert brief.ui_impacting is True          # gates the conditional UX-mocks stage
    assert brief.complexity == "medium"        # early scope signal carried onto the brief
    assert brief.project == "meal-planner"     # carried from the event for downstream context
    assert brief.cost_usd == pytest.approx(0.02)  # 1500×$5/1e6 + 500×$25/1e6
    assert provider.calls[0]["tier"] == "opus"  # brief itself stays Opus (it makes the call)


# --- runner-backed bug-prioritization activity (M3 Haiku swap, bug path) -------
def test_prioritize_bug_activity_adapts_contract_and_sees_triage():
    from orchestrator.activities.agent_backed import prioritize_bug_with_runner
    from orchestrator.agents.registry.contracts import BugPriorityOutput
    from orchestrator.shared.types import BugPriority, FeedbackKind, Triage

    parsed = BugPriorityOutput(priority="P0", rationale="double-charges every user; revenue + trust")
    provider = _FakeProvider(parsed, 800, 100, model_id="claude-haiku-4-5")  # haiku $1/$5
    event = SimpleNamespace(id="x", kind=FeedbackKind.BUG, title="double charge",
                            body="charged twice", submitted_by="t", project="meal-planner")
    triage = Triage(kind=FeedbackKind.BUG, priority="P1", needs_clarification=False)

    result = prioritize_bug_with_runner(provider, event, triage)

    assert isinstance(result, BugPriority)
    assert result.priority == "P0"          # PM can override the triage P1 read
    assert result.cost_usd == pytest.approx(0.0013)  # 800×$1/1e6 + 100×$5/1e6
    assert provider.calls[0]["tier"] == "haiku"
    # the triage's initial read is provided to the PM as context
    assert "P1" in provider.calls[0]["messages"][0]["content"]


# --- runner-backed council vote activity (M3 Sonnet swap) ----------------------
def test_council_vote_activity_adapts_contract_and_selects_lens():
    from orchestrator.activities.agent_backed import council_vote_with_runner
    from orchestrator.agents.registry.contracts import CouncilVoteOutput
    from orchestrator.shared.types import Brief, Vote

    parsed = CouncilVoteOutput(approve=False, rationale="handles sensitive data without consent")
    provider = _FakeProvider(parsed, 2000, 100, model_id="claude-sonnet-5")  # sonnet $3/$15
    brief = Brief(summary="s", problem="p", target_users="u", ui_impacting=True,
                  project="meal-planner")

    result = council_vote_with_runner(provider, "legal", brief)

    assert isinstance(result, Vote)
    assert result.voter == "legal"          # workflow voter id preserved
    assert result.approve is False
    assert result.cost_usd == pytest.approx(0.0075)  # 2000×$3/1e6 + 100×$15/1e6
    # The legal voter id selected the legal-lens persona (its prompt, on sonnet).
    assert "LEGAL" in provider.calls[0]["system"]
    assert provider.calls[0]["tier"] == "sonnet"


# --- runner-backed synthetic-user activity (M3 Sonnet swap) --------------------
def test_research_finding_activity_adapts_contract_and_embodies_demographic():
    from orchestrator.activities.agent_backed import research_finding_with_runner
    from orchestrator.agents.registry.contracts import ResearchFindingOutput
    from orchestrator.shared.types import PRD, ResearchFinding

    parsed = ResearchFindingOutput(sentiment="positive", notes="saves me 30 min/week")
    provider = _FakeProvider(parsed, 1500, 80, model_id="claude-sonnet-5")  # sonnet
    prd = PRD(feature_id="feat-x", version=1, content="auto-plan button", project="meal-planner")

    result = research_finding_with_runner(provider, "time-constrained professional", prd)

    assert isinstance(result, ResearchFinding)
    assert result.persona == "time-constrained professional"  # demographic preserved
    assert result.sentiment == "positive"
    assert result.cost_usd == pytest.approx(0.0057)  # 1500×$3/1e6 + 80×$15/1e6
    # The demographic is injected via the task input, not the system prompt.
    assert "time-constrained professional" in provider.calls[0]["messages"][0]["content"]
    assert provider.calls[0]["tier"] == "sonnet"


# --- runner-backed PRD-authoring activity (M3 Opus swap) -----------------------
def test_author_prd_activity_mints_id_and_carries_project():
    from orchestrator.activities.agent_backed import author_prd_with_runner
    from orchestrator.agents.registry.contracts import PRDAuthoringOutput
    from orchestrator.shared.types import PRD, Brief

    parsed = PRDAuthoringOutput(
        content="# PRD\n## Acceptance criteria\n- works",
        acceptance_criteria=["works"],
        open_issues=["which auth?"],
    )
    provider = _FakeProvider(parsed, 4000, 1200, model_id="claude-opus-4-8")  # opus $5/$25
    brief = Brief(summary="Surprise me button", problem="p", target_users="u",
                  ui_impacting=True, project="meal-planner")

    prd = author_prd_with_runner(provider, brief)

    assert isinstance(prd, PRD)
    assert prd.feature_id == "feat-surprise-me-button"  # minted from summary (shared helper)
    assert prd.version == 1
    assert prd.project == "meal-planner"
    assert prd.open_issues == ["which auth?"]
    assert prd.cost_usd == pytest.approx(0.05)  # 4000×$5/1e6 + 1200×$25/1e6
    assert provider.calls[0]["tier"] == "opus"


# --- runner-backed PRD-revision activity (M3 Sonnet swap) ----------------------
def test_revise_prd_activity_bumps_version_and_preserves_identity():
    from orchestrator.activities.agent_backed import revise_prd_with_runner
    from orchestrator.agents.registry.contracts import PRDRevisionOutput
    from orchestrator.shared.types import PRD, ArchitectReview

    parsed = PRDRevisionOutput(
        content="PRD v2 — added a rate-limit section",
        open_issues=[],
        changelog="addressed the abuse concern with rate limiting",
    )
    provider = _FakeProvider(parsed, 3000, 400, model_id="claude-sonnet-5")  # sonnet
    prd = PRD(feature_id="feat-x", version=1, content="PRD v1", project="meal-planner")
    review = ArchitectReview(approved=False, pass_no=1, concerns=["no abuse protection"])

    revised = revise_prd_with_runner(provider, prd, review)

    assert isinstance(revised, PRD)
    assert revised.version == 2                 # version bump owned by the activity, not the LLM
    assert revised.feature_id == "feat-x"       # identity preserved
    assert revised.project == "meal-planner"    # project context carried forward
    assert revised.content == "PRD v2 — added a rate-limit section"
    assert revised.cost_usd == pytest.approx(0.015)  # 3000×$3/1e6 + 400×$15/1e6
    # The concern was injected into the task input for the model to address.
    assert "no abuse protection" in provider.calls[0]["messages"][0]["content"]
    assert provider.calls[0]["tier"] == "sonnet"


# --- runner-backed architect-review activity (M3 Opus swap) --------------------
def test_review_prd_activity_preserves_pass_no_and_adapts_verdict():
    from orchestrator.activities.agent_backed import review_prd_with_runner
    from orchestrator.agents.registry.contracts import ArchitectReviewOutput
    from orchestrator.shared.types import PRD, ArchitectReview

    parsed = ArchitectReviewOutput(approved=False, concerns=["acceptance criteria are untestable"])
    provider = _FakeProvider(parsed, 2000, 400, model_id="claude-opus-4-8")  # opus $5/$25
    prd = PRD(feature_id="feat-x", version=2, content="PRD body", project="meal-planner")

    review = review_prd_with_runner(provider, prd, pass_no=3)

    assert isinstance(review, ArchitectReview)
    assert review.pass_no == 3                  # loop control owned by the activity, not the LLM
    assert review.approved is False
    assert review.concerns == ["acceptance criteria are untestable"]
    assert review.cost_usd == pytest.approx(0.02)  # 2000×$5/1e6 + 400×$25/1e6
    assert provider.calls[0]["tier"] == "opus"


# --- runner-backed story-planning activity (M3 Opus swap) ----------------------
def test_plan_stories_activity_mints_ids_and_adapts_stories():
    from orchestrator.activities.agent_backed import plan_stories_with_runner
    from orchestrator.agents.registry.contracts import PlannedStory, StoryPlanOutput
    from orchestrator.shared.types import PRD, ResearchReport, StoryPlan

    parsed = StoryPlanOutput(complexity="medium", stories=[
        PlannedStory(title="backend: recommendation endpoint", estimate=3),
        PlannedStory(title="frontend: Surprise Me button", estimate=2),
    ])
    provider = _FakeProvider(parsed, 2000, 600, model_id="claude-opus-4-8")  # opus
    prd = PRD(feature_id="feat-surprise", version=2, content="PRD", project="meal-planner")
    report = ResearchReport(feature_id="feat-surprise", findings=[], overall_sentiment="positive",
                            summary_ref="artifact://r")

    plan = plan_stories_with_runner(provider, prd, report)

    assert isinstance(plan, StoryPlan)
    assert plan.feature_id == "feat-surprise"
    assert [s.id for s in plan.stories] == ["feat-surprise-S1", "feat-surprise-S2"]  # ids minted here
    assert [s.title for s in plan.stories] == ["backend: recommendation endpoint",
                                               "frontend: Surprise Me button"]
    assert [s.estimate for s in plan.stories] == [3, 2]
    assert plan.complexity == "medium"          # scope signal surfaced onto the workflow plan
    assert plan.cost_usd == pytest.approx(0.025)  # 2000×$5/1e6 + 600×$25/1e6
    assert provider.calls[0]["tier"] == "opus"


def test_plan_stories_selects_coding_model_per_story_complexity():
    """The model-selection phase: the architect's per-story complexity read picks the coding
    tier carried to the pod — a complex story -> opus, a simple one -> sonnet."""
    from orchestrator.activities.agent_backed import plan_stories_with_runner
    from orchestrator.agents.registry.contracts import PlannedStory, StoryPlanOutput
    from orchestrator.shared.types import PRD, ResearchReport

    parsed = StoryPlanOutput(complexity="large", stories=[
        PlannedStory(title="schema migration + concurrency", estimate=5, complexity="complex"),
        PlannedStory(title="add a settings toggle", estimate=1, complexity="simple"),
    ])
    provider = _FakeProvider(parsed, 2000, 600, model_id="claude-opus-4-8")
    prd = PRD(feature_id="feat-x", version=1, content="PRD", project="meal-planner")
    report = ResearchReport(feature_id="feat-x", findings=[], overall_sentiment="positive",
                            summary_ref="artifact://r")

    plan = plan_stories_with_runner(provider, prd, report)

    assert [s.tier for s in plan.stories] == ["opus", "sonnet"]


def test_planned_story_defaults_to_simple_when_unclassified():
    """Back-compat: a story the model leaves unclassified defaults to simple (the cheaper tier)."""
    from orchestrator.agents.registry.contracts import PlannedStory

    assert PlannedStory(title="x", estimate=1).complexity == "simple"


# --- runner-backed code reviewer (pre-PR review loop, reasoning plane) ----------
def test_review_diff_activity_adapts_contract_and_carries_required_changes():
    from orchestrator.activities.agent_backed import review_diff_with_runner
    from orchestrator.agents.registry.contracts import CodeReviewOutput
    from orchestrator.shared.types import ReviewResult, Story, StoryPlan, StoryResult

    parsed = CodeReviewOutput(
        approved=False,
        required_changes=["toggle has no persisted state", "missing aria-label"],
        summary="Solid start but the toggle does not persist.",
    )
    provider = _FakeProvider(parsed, 1200, 200, model_id="claude-sonnet-5")  # sonnet $3/$15
    plan = StoryPlan(
        feature_id="feat-dark", project="meal-planner",
        stories=[Story(id="feat-dark-S1", title="Add dark-mode toggle", estimate=2)],
    )
    result = StoryResult(
        story_id="feat-dark", status="done", pr_ref="", summary="added toggle",
        diff="diff --git a/components/Toggle.tsx b/components/Toggle.tsx\n"
             "--- a/components/Toggle.tsx\n+++ b/components/Toggle.tsx\n+const Toggle = () => null\n",
    )

    review = review_diff_with_runner(provider, plan, result)

    assert isinstance(review, ReviewResult)
    assert review.approved is False
    assert review.required_changes == ["toggle has no persisted state", "missing aria-label"]
    assert review.notes == "Solid start but the toggle does not persist."
    assert review.cost_usd == pytest.approx(0.0066)  # 1200×$3/1e6 + 200×$15/1e6
    # The planned story, the diff hunk, AND the complete changed-file list all reach the reviewer.
    sent = provider.calls[0]["messages"][0]["content"]
    assert "Add dark-mode toggle" in sent and "+const Toggle = () => null" in sent
    assert "components/Toggle.tsx" in sent  # full file list so a present file can't read as missing


def test_review_diff_degrades_to_non_blocking_when_reviewer_cannot_parse():
    """A reviewer that can't produce schema-valid output must NOT raise: it runs AFTER the
    expensive coding pass, so raising kills the whole feature workflow and discards the diff
    (observed with Sonnet on the Vercel gateway 2026-06-30). It degrades to a non-blocking
    pass (approved=True) — never approved=False, which would burn a full coding revise per
    MAX_REVIEW_PASSES on a systematic parse failure — and lets CI be the hard gate (§8a)."""
    from orchestrator.activities.agent_backed import review_diff_with_runner
    from orchestrator.shared.types import ReviewResult, Story, StoryPlan, StoryResult

    provider = _FakeProvider(payload=None, in_tok=100, out_tok=10)  # never parses -> runner raises
    plan = StoryPlan(
        feature_id="feat-x", project="meal-planner",
        stories=[Story(id="feat-x-S1", title="do a thing", estimate=1)],
    )
    result = StoryResult(story_id="feat-x", status="done", pr_ref="", summary="did it",
                         diff="diff --git a/a.ts b/a.ts\n+const a = 1\n")

    review = review_diff_with_runner(provider, plan, result)

    assert isinstance(review, ReviewResult)
    assert review.approved is True          # non-blocking, so the pod proceeds to open_pr
    assert review.required_changes == []    # not a blind revise trigger
    assert "unavailable" in review.notes.lower()
    assert len(provider.calls) == 2         # code_reviewer max_reask=1 -> 2 attempts, then degrade


def test_qa_review_weighs_diff_and_status_not_just_the_developer_summary():
    """The real QA agent must reach the provider the OBJECTIVE status and the actual diff (not
    only the developer's self-report), and adapt the contract to a QAResult — so an optimistic
    summary over an empty/failed diff can be judged a fail (the audit-accuracy fix, 2026-06-28)."""
    from orchestrator.activities.agent_backed import qa_review_with_runner
    from orchestrator.agents.registry.contracts import QAReviewOutput
    from orchestrator.shared.types import QAResult, StoryResult

    parsed = QAReviewOutput(passed=False, notes="Summary claims a toggle but the diff is empty.")
    provider = _FakeProvider(parsed, 900, 80, model_id="claude-sonnet-5")  # sonnet $3/$15
    result = StoryResult(
        story_id="feat-dark", status="failed", pr_ref="", diff="",
        summary="Everything looks correct — added the dark-mode toggle.",
    )

    qa = qa_review_with_runner(provider, "meal-planner", [result])

    assert isinstance(qa, QAResult)
    assert qa.passed is False
    assert qa.notes == "Summary claims a toggle but the diff is empty."
    assert qa.cost_usd == pytest.approx(0.0039)  # 900×$3/1e6 + 80×$15/1e6
    # Both the objective status and the developer summary reach the QA persona.
    sent = provider.calls[0]["messages"][0]["content"]
    assert "status: failed" in sent and "Everything looks correct" in sent
    assert provider.calls[0]["tier"] == "sonnet"


def test_render_diff_for_review_truncates_per_file_and_reports_every_elided_file():
    """The truncation fix (regression from the live run, 2026-06-21): a big leading file must
    not push later files out of view silently — every changed file stays in the list, and any
    file whose hunks were cut/omitted is reported so it can be logged for traceability."""
    from orchestrator.activities.agent_backed import _MAX_REVIEW_DIFF_CHARS, _render_diff_for_review

    big = "+" + "x" * (_MAX_REVIEW_DIFF_CHARS + 500) + "\n"
    diff = (
        "diff --git a/big.ts b/big.ts\n--- a/big.ts\n+++ b/big.ts\n" + big
        + "diff --git a/db/schema.ts b/db/schema.ts\nnew file mode 100644\n"
        "--- /dev/null\n+++ b/db/schema.ts\n+export const feedback = table()\n"
    )

    rendered, changed, truncated = _render_diff_for_review(diff)

    assert changed == ["big.ts", "db/schema.ts"]   # complete set regardless of budget
    assert "db/schema.ts" in truncated             # the elided file is reported (for the log)
    assert "db/schema.ts" in rendered              # and still visible by name in the rendered diff
    # A small diff is passed through untouched, nothing reported truncated.
    r2, c2, t2 = _render_diff_for_review("diff --git a/x b/x\n--- a/x\n+++ b/x\n+a\n")
    assert c2 == ["x"] and t2 == [] and "+a" in r2


def test_review_diff_logs_truncated_files_and_tells_reviewer_theyre_present(caplog):
    """Traceability: when the diff is truncated, the elided files are named in a WARNING log AND
    the reviewer is explicitly told they're present (not missing) — what caused the false
    'missing schema/migration' rejection before the fix."""
    import logging

    from orchestrator.activities.agent_backed import _MAX_REVIEW_DIFF_CHARS, review_diff_with_runner
    from orchestrator.agents.registry.contracts import CodeReviewOutput
    from orchestrator.shared.types import Story, StoryPlan, StoryResult

    parsed = CodeReviewOutput(approved=True, required_changes=[], summary="ok")
    provider = _FakeProvider(parsed, 1000, 100, model_id="claude-sonnet-5")
    plan = StoryPlan(feature_id="feat-x", project="meal-planner",
                     stories=[Story(id="S1", title="Add feedback table", estimate=3)])
    big = "+" + "x" * (_MAX_REVIEW_DIFF_CHARS + 500) + "\n"
    result = StoryResult(
        story_id="feat-x", status="done", pr_ref="", summary="impl",
        diff=("diff --git a/big.ts b/big.ts\n--- a/big.ts\n+++ b/big.ts\n" + big
              + "diff --git a/db/schema.ts b/db/schema.ts\n--- a/db/schema.ts\n+++ b/db/schema.ts\n+feedback\n"),
    )

    with caplog.at_level(logging.WARNING, logger="orchestrator.activities.agent_backed"):
        review_diff_with_runner(provider, plan, result)

    assert any("db/schema.ts" in r.getMessage() for r in caplog.records)  # named in the log
    sent = provider.calls[0]["messages"][0]["content"]
    assert "db/schema.ts" in sent and "Do NOT" in sent  # reviewer told it's present, not missing
