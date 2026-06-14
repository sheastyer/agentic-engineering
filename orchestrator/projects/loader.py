"""Project Profile loader. Adding a project = registering its profile here, nothing more."""

from orchestrator.projects import meal_planner
from orchestrator.projects.profile import ProjectProfile

# id -> profile. One line per target project.
_PROFILES: dict[str, ProjectProfile] = {
    meal_planner.PROFILE.id: meal_planner.PROFILE,
}


def load_profile(project_id: str) -> ProjectProfile:
    """Return the validated profile for project_id. Raises KeyError if unknown,
    ValueError if the profile is malformed."""
    try:
        profile = _PROFILES[project_id]
    except KeyError:
        raise KeyError(
            f"no Project Profile registered for {project_id!r}; "
            f"known: {sorted(_PROFILES)}"
        ) from None
    profile.validate()
    return profile


def known_projects() -> list[str]:
    return sorted(_PROFILES)
