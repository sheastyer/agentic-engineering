"""Shared id derivation. Kept generic (no project knowledge) so both the stub and the
real PRD-authoring activity mint the same feature id from a brief summary."""


def feature_id(summary: str) -> str:
    slug = "".join(c if c.isalnum() else "-" for c in summary.lower()).strip("-")
    return f"feat-{slug[:32]}"
