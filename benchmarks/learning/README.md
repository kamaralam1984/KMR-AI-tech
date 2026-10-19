# Learning-support evaluation

`cases.json` holds the labeled questions for the typed-question solver (`backend/app/assistant/learning.py`).
Regenerate with `python3 build_cases.py`. Run with `make learning-eval` (or
`cd backend && .venv/bin/python -m app.assistant.evaluate`).

**What this measures:** whether the solver returns the exact expected result, asks for clarification
when a question is ambiguous or unreadable, refuses dangerous or oversized input, and respects the
age-band level. Expected values are built by construction (a chosen `x`, a Python integer
expression), not copied from the solver.

**What it does not measure:** teaching quality, the quality of the quoted school material, or
whether explanations suit a real child. The cases are written by the author and have **not** been
reviewed by a teacher. Educational-quality evaluation by qualified people is still required before
the learning module can be called production-ready, and that is why the API reports
`production_ready: false`.
