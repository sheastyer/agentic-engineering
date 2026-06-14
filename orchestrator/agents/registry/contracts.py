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
