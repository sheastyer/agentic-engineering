"""Output contracts — the structured-output schema each persona is constrained to.

Pydantic models keep the LLM's output machine-checkable (the M2 `CON` eval validates
against these). Activities adapt a contract instance into the workflow's plain dataclass
(orchestrator/shared/types.py), keeping the LLM-facing schema separate from the
replay-serialized workflow data.
"""

from typing import Literal

from pydantic import BaseModel, Field


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


class StoryPlanOutput(BaseModel):
    stories: list[PlannedStory] = Field(
        description="The PRD broken into independently shippable, vertically-sliced stories, "
        "ordered by a sensible build sequence and collectively covering its acceptance "
        "criteria without inventing scope."
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
