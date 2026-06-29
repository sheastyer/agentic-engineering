"""Output contracts — the structured-output schema each persona is constrained to.

Pydantic models keep the LLM's output machine-checkable (the M2 `CON` eval validates
against these). Activities adapt a contract instance into the workflow's plain dataclass
(orchestrator/shared/types.py), keeping the LLM-facing schema separate from the
replay-serialized workflow data.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class TriageOutput(BaseModel):
    kind: Literal["bug", "feature"]
    priority: Literal["P0", "P1", "P2", "P3"]
    needs_clarification: bool = Field(
        description="True only if the report is too vague to act on without a question."
    )
    rationale: str


class BriefOutput(BaseModel):
    summary: str
    problem: str
    target_users: str
    ui_impacting: bool = Field(description="Whether the feature plausibly touches the UI.")
    complexity: Literal["small", "medium", "large"] = Field(
        description="Honest read of the WHOLE feature's build size. small = a focused change "
        "(most single-control UI tweaks: a toggle, a button, a setting); medium = several "
        "coordinated slices; large = a substantial multi-part feature. This is an early cost "
        "signal — the downstream reasoning stages run on a cheaper model for small features, "
        "so do NOT inflate it."
    )


class BugPriorityOutput(BaseModel):
    priority: Literal["P0", "P1", "P2", "P3"] = Field(
        description="Final bug priority by user impact/severity (P0 critical: data loss, "
        "security, widespread outage … P3 trivial/cosmetic). May override the triage read."
    )
    rationale: str = Field(
        description="One terse sentence on the impact driving the priority, and whether it "
        "agrees with or overrides the triage priority."
    )


class CouncilVoteOutput(BaseModel):
    approve: bool = Field(
        description="Whether this council member approves taking the feature forward, "
        "judged strictly from this member's lens (legal risk / commercial value)."
    )
    rationale: str = Field(
        description="One or two sentences justifying the vote from this member's lens."
    )


class ResearchFindingOutput(BaseModel):
    sentiment: Literal["positive", "neutral", "negative"] = Field(
        description="This synthetic user's overall reaction to the proposed feature."
    )
    notes: str = Field(
        description="1-3 sentences in this user's voice: what they'd like, dislike, or "
        "want changed about the feature."
    )


class PRDAuthoringOutput(BaseModel):
    content: str = Field(
        description="The full PRD body as markdown: problem & context, goals and non-goals, "
        "user stories, acceptance criteria, UX notes, and risks/open questions."
    )
    acceptance_criteria: list[str] = Field(
        description="The PRD's acceptance criteria as a flat checklist (also embedded in "
        "`content`); each item testable and unambiguous."
    )
    open_issues: list[str] = Field(
        default_factory=list,
        description="Unresolved questions a reviewer/architect must weigh in on.",
    )


class ArchitectReviewOutput(BaseModel):
    approved: bool = Field(
        description="True only if the PRD is technically sound and complete enough to break "
        "into stories and build, with no blocking gaps."
    )
    concerns: list[str] = Field(
        default_factory=list,
        description="Specific, actionable technical concerns the PM must resolve before "
        "approval (empty iff approved). Each names a concrete gap — ambiguous/untestable "
        "requirement, missing edge case, infeasible or under-specified technical point — "
        "not vague unease.",
    )


class PlannedStory(BaseModel):
    title: str = Field(
        description="A concise, implementation-oriented story title — a vertical slice that "
        "can be built and shipped independently."
    )
    estimate: int = Field(
        ge=1, le=8,
        description="Relative effort in story points (1=trivial … 8=large). Split anything "
        "larger than 8 into multiple stories.",
    )


# Story-count ceiling per declared complexity — the scope signal that stops the architect
# over-decomposing a simple feature (the ~10-story "add a toggle" plan, incl. standalone
# accessibility-audit stories). Enforced in the validator so a violation re-asks the model.
_COMPLEXITY_MAX_STORIES = {"small": 3, "medium": 6, "large": 10}


class StoryPlanOutput(BaseModel):
    complexity: Literal["small", "medium", "large"] = Field(
        description="Your honest read of the WHOLE feature's build size, which bounds how many "
        "stories are reasonable. small = a focused change one engineer ships in a sitting "
        "(most UI tweaks: a toggle, a button, a setting); medium = several coordinated slices; "
        "large = a substantial multi-part feature. Do NOT inflate this to justify more stories."
    )
    stories: list[PlannedStory] = Field(
        description="The PRD broken into independently shippable, vertically-sliced stories, "
        "ordered by a sensible build sequence and collectively covering its acceptance "
        "criteria without inventing scope. Prefer the FEWEST slices that deliver the feature."
    )

    @model_validator(mode="after")
    def _bound_stories_to_complexity(self) -> "StoryPlanOutput":
        if not self.stories:
            raise ValueError("a plan must contain at least one story")
        ceiling = _COMPLEXITY_MAX_STORIES[self.complexity]
        if len(self.stories) > ceiling:
            raise ValueError(
                f"a {self.complexity} feature should be at most {ceiling} stories, got "
                f"{len(self.stories)}. Fold testing/accessibility/CI/docs criteria into the "
                "implementing stories rather than making them standalone stories — or, if the "
                "feature genuinely is larger, raise the complexity."
            )
        return self


class CodeReviewOutput(BaseModel):
    approved: bool = Field(
        description="True only if the diff correctly and completely implements the planned "
        "stories, follows the project's conventions, and is safe to ship — no blocking issues."
    )
    required_changes: list[str] = Field(
        default_factory=list,
        description="Specific, actionable changes the developer must make before this can be "
        "approved (empty iff approved). Each names a concrete problem — a missing story, a "
        "bug, a convention violation, an untested edge case, a security/regression risk — not "
        "vague unease. The developer revises against exactly these.",
    )
    summary: str = Field(
        description="One or two sentences summarizing the review verdict for the PR body and "
        "the human reviewer (what the diff does well and, if rejected, the gist of what's wrong)."
    )


class QAReviewOutput(BaseModel):
    passed: bool = Field(
        description="True only if the change is functionally sound and ready for a human to "
        "review at the deploy gate: the diff substantiates the work the developer claims, has "
        "no obvious functional gaps (a claimed user-facing behavior with no supporting code, an "
        "empty or contradictory diff), and the objective build/test status is not failing. A "
        "passing self-report over an empty or broken diff is NOT a pass."
    )
    notes: str = Field(
        description="One to three sentences: the QA verdict for the human at the deploy gate — "
        "what was verified, and if failed, the concrete functional gap (not vague unease)."
    )


class PRDRevisionOutput(BaseModel):
    content: str = Field(
        description="The full revised PRD body, edited to resolve every raised concern "
        "while preserving the rest of the document."
    )
    open_issues: list[str] = Field(
        default_factory=list,
        description="Concerns that remain unresolved after this revision (empty if all "
        "were addressed).",
    )
    changelog: str = Field(
        description="One line naming, concretely, what changed in this revision and which "
        "concern(s) each change resolves."
    )
