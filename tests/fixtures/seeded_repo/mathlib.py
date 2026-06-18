"""A trivial library with one SEEDED BUG, used as the M4 coding-loop fixture.

This is NOT org code — it's a throwaway target the engineering pod operates on in a
managed workspace, so we can prove the implement -> test -> QA loop end-to-end without
touching the real meal-planner. `add` is wrong on purpose; the seeded task is to fix it.
"""


def add(a, b):
    # SEEDED BUG: subtraction where addition is intended. The pod's job is to fix this.
    return a - b


def multiply(a, b):
    return a * b
