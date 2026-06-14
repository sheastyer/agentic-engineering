"""Automated regression checks R3 (workflow purity) and R4 (no secrets).

R3 is the FIRST thing built in M2 (PLAN.md), deliberately landing before the first real
model client exists — so the Anthropic SDK can never be accidentally imported into a
workflow during this milestone. These are static-source checks (AST + regex), so they
run in milliseconds and never execute workflow code.
"""

import ast
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO / "orchestrator" / "workflows"

# Modules a workflow must never import: model SDKs, network, raw clock/random, file/db I/O.
# (timedelta is fine — it's deterministic; datetime.now()/time.time() are caught below.)
FORBIDDEN_IMPORTS = {
    "anthropic", "openai", "requests", "httpx", "aiohttp", "urllib", "socket",
    "random", "secrets", "sqlite3", "subprocess", "pathlib",
    "orchestrator.agents",  # the Agent Runner is called from activities, never workflows
}
# Nondeterministic call/source patterns banned anywhere in workflow source.
FORBIDDEN_PATTERNS = [
    re.compile(r"\bdatetime\.now\("),
    re.compile(r"\bdate\.today\("),
    re.compile(r"\btime\.time\("),
    re.compile(r"\brandom\."),
    re.compile(r"\bopen\("),
    re.compile(r"\brequests\."),
    re.compile(r"\bhttpx\."),
]


def _workflow_files():
    return sorted(p for p in WORKFLOWS_DIR.glob("*.py") if p.name != "__init__.py")


@pytest.mark.parametrize("path", _workflow_files(), ids=lambda p: p.name)
def test_workflow_module_imports_are_pure(path):
    """R3: top-level imports in workflow modules contain no nondeterministic/SDK deps.

    Imports inside `with workflow.unsafe.imports_passed_through():` are pass-through to
    the host and are exempt — but our workflows only pass through activity refs + types,
    so we check that nothing forbidden is imported at all."""
    tree = ast.parse(path.read_text(), filename=str(path))
    offenders = []
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            root = name.split(".")[0]
            if root in FORBIDDEN_IMPORTS or name in FORBIDDEN_IMPORTS:
                offenders.append(name)
    assert not offenders, f"{path.name} imports forbidden-in-workflow modules: {offenders}"


@pytest.mark.parametrize("path", _workflow_files(), ids=lambda p: p.name)
def test_workflow_source_has_no_nondeterministic_calls(path):
    """R3: workflow source contains no clock reads, randomness, file/network I/O."""
    src = path.read_text()
    hits = [pat.pattern for pat in FORBIDDEN_PATTERNS if pat.search(src)]
    assert not hits, f"{path.name} contains nondeterministic patterns: {hits}"


def test_no_secrets_in_source():
    """R4: no hardcoded API keys/credentials in tracked source."""
    secret_patterns = [
        re.compile(r"sk-ant-[A-Za-z0-9-]{8,}"),         # Anthropic API key
        re.compile(r"whsec_[A-Za-z0-9]{8,}"),           # webhook signing secret
        re.compile(r"ANTHROPIC_API_KEY\s*=\s*['\"]\S"),  # assigned a literal value
    ]
    skip_dirs = {".venv", ".git", "__pycache__", ".pytest_cache", "node_modules"}
    offenders = []
    for path in REPO.rglob("*"):
        if path.is_dir() or any(part in skip_dirs for part in path.parts):
            continue
        if path.suffix not in {".py", ".toml", ".md", ".json", ".yaml", ".yml", ".env"}:
            continue
        if path.name == "test_invariants.py":  # this file names the patterns
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for pat in secret_patterns:
            if pat.search(text):
                offenders.append(f"{path.relative_to(REPO)} :: {pat.pattern}")
    assert not offenders, f"possible secrets in source: {offenders}"
