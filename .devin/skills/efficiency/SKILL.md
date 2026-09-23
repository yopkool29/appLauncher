---
name: efficiency
description: Solve the task with the smallest possible code change. Use for focused bug fixes or small feature requests where minimal diff matters.
---

Solve the task with the smallest possible code change.

Rules:
- Prefer modifying existing code over introducing new abstractions.
- Do not add classes, interfaces, helpers, wrappers, configuration,
  dependencies, or files unless strictly necessary.
- Do not refactor unrelated code.
- Do not design for hypothetical future requirements.
- First determine whether zero code changes are sufficient.
- If changes are necessary, find the smallest change that satisfies
  the requirements.
- After implementing, perform a minimization pass:
  actively try to remove code while preserving correctness.
- Every added piece of code must directly contribute to a requirement.

Before finishing:
1. Run the relevant tests.
2. Inspect the diff.
3. Remove anything that is not necessary.
4. Report why each remaining changed file is necessary.

Assume that the current implementation is overengineered. Your default hypothesis should be that substantial amounts of code can be removed. Try to delete before you add.