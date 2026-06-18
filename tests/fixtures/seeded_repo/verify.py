"""Fixture test suite — the target repo's own `test_command` runs THIS file.

Deliberately named `verify.py` (not `test_*.py`) so the org's top-level pytest run does
NOT auto-collect it — it lives behind testpaths but only executes inside a workspace via
the fixture profile's test command (`python -m pytest -q verify.py`). With the seeded bug
in mathlib.add, `test_add` fails; a correct fix turns the suite green.
"""

from mathlib import add, multiply


def test_add():
    assert add(2, 3) == 5


def test_add_negative():
    assert add(-1, -1) == -2


def test_multiply():
    assert multiply(2, 3) == 6
