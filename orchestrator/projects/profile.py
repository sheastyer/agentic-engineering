"""The Project Profile — the ONLY place project-specific knowledge lives (CLAUDE.md §3, §9.8).

The org (workflows, runner, personas) is generic; a profile describes a target app as
data. Adding a new project = writing a new profile, never editing the org. Profiles hold
*references* to secrets (env var names), never secret values (§9.3).
"""

from dataclasses import dataclass, field
from enum import StrEnum

# StrEnum, not (str, Enum) — same temporalio-converter rationale as shared/types.py:
# a (str, Enum) type hint decodes as a char list if the value ever crosses an
# activity boundary. Profiles are loaded locally today, but keep the enums safe.


class IntakeKind(StrEnum):
    """How feedback enters the org for this project (the M5 intake adapter implements it)."""

    DB_TABLE = "db_table"
    WEBHOOK = "webhook"
    API = "api"
    FILE_DROP = "file_drop"
    MANUAL = "manual"


class DeployKind(StrEnum):
    """What 'deploy' concretely means for this project (always behind a human gate, §9.2)."""

    OPEN_PR = "open_pr"
    MERGE = "merge"
    CONTAINER_PUSH = "container_push"
    ENVIRONMENT = "environment"


@dataclass
class Repo:
    git_remote: str
    default_branch: str
    local_path: str = ""  # optional; the managed per-run workspace clones from git_remote (D4)


@dataclass
class Stack:
    languages: list[str]
    package_manager: str
    test_command: str
    build_command: str = ""
    # Can `test_command` actually run inside the org's coding sandbox? False for targets
    # whose suite needs things the sandbox denies (network installs, browsers, a DB) — the
    # pod then reports QA "unavailable" (honest) instead of "failed" (misleading), and the
    # PR's CI is the objective gate. This knob living HERE (not org config) is deliberate:
    # it's per-target knowledge (§3).
    sandbox_tests: bool = True


@dataclass
class Intake:
    kind: IntakeKind
    # Free-form descriptor the adapter needs (table name, webhook path, endpoint).
    descriptor: str = ""


@dataclass
class Deploy:
    kind: DeployKind
    descriptor: str = ""


@dataclass
class ProjectProfile:
    # identity + domain context the agents need
    id: str
    name: str
    description: str
    repo: Repo
    stack: Stack
    intake: Intake
    deploy: Deploy
    conventions: list[str] = field(default_factory=list)
    # references to credentials in the secret store: logical name -> env var name (NOT a value)
    secret_refs: dict[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        """Raise ValueError on a malformed profile. Cheap structural checks only."""
        if not self.id or not self.id.strip():
            raise ValueError("profile.id is required")
        if not self.name or not self.description:
            raise ValueError(f"profile {self.id!r}: name and description are required")
        if not self.repo.git_remote:
            raise ValueError(f"profile {self.id!r}: repo.git_remote is required")
        if not self.repo.default_branch:
            raise ValueError(f"profile {self.id!r}: repo.default_branch is required")
        if not self.stack.languages:
            raise ValueError(f"profile {self.id!r}: stack.languages must be non-empty")
        if not self.stack.test_command:
            raise ValueError(f"profile {self.id!r}: stack.test_command is required (M4 runs it)")
        if not isinstance(self.intake.kind, IntakeKind):
            raise ValueError(f"profile {self.id!r}: intake.kind must be an IntakeKind")
        if not isinstance(self.deploy.kind, DeployKind):
            raise ValueError(f"profile {self.id!r}: deploy.kind must be a DeployKind")
        # secret_refs must be references (env var names), never inline secrets.
        for logical, ref in self.secret_refs.items():
            if any(marker in ref for marker in ("sk-ant-", "whsec_")) or len(ref) > 64:
                raise ValueError(
                    f"profile {self.id!r}: secret_ref {logical!r} looks like a value, "
                    f"not an env-var name reference"
                )
