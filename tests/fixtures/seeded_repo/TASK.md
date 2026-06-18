# Seeded task: fix the broken `add`

`mathlib.add(a, b)` returns the wrong result — it subtracts instead of adding. The test
suite (`verify.py`) fails because of it.

**Fix `mathlib.add` so it returns the sum of its arguments.** Keep the change minimal and
do not modify the tests. The suite passes when `add` is correct.
