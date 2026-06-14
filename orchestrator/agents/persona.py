"""The persona contract (CLAUDE.md §6).

A persona is config, not code: one generic Agent Runner executes any persona. A persona =
system prompt + toolset + model tier + output contract + context policy + termination.
For M2 the output contract is a Pydantic model (the LLM is constrained to it via the
SDK's structured outputs); project-specific context is injected at runtime from the
Project Profile, never baked into the prompt.
"""

from dataclasses import dataclass, field

from pydantic import BaseModel

from orchestrator.projects.profile import ProjectProfile


@dataclass(frozen=True)
class Persona:
    name: str
    tier: str  # "haiku" | "sonnet" | "opus" (maps to PRICING / model id)
    # System prompt template. `{project}` / `{domain}` are filled from the profile so the
    # same persona works for any target app — project knowledge is injected, not baked in.
    system_template: str
    output_model: type[BaseModel]  # the output contract
    tools: tuple = ()              # reasoning personas have none in M2; coding tools arrive M4
    effort: str = "medium"         # output_config.effort for sonnet/opus tiers
    max_reask: int = 1             # bounded re-ask on malformed output, then hard fail
    max_tokens: int = 4096

    def render_system(self, profile: ProjectProfile) -> str:
        """Inject project/domain context into the system prompt at runtime."""
        return self.system_template.format(
            project=profile.name,
            domain=profile.description,
            conventions="\n".join(f"- {c}" for c in profile.conventions),
        )
