# Minimal code — always applied

Fusion of the `efficiency` and `optimisze` skills. Applies to every change in this repo.

## Principles

- Solve the task with the **smallest possible code change**.
- Prefer modifying existing code over new abstractions.
- No new classes, interfaces, helpers, wrappers, config, dependencies or
  files unless strictly necessary.
- Do not refactor unrelated code; do not design for hypothetical needs.
- First check whether **zero code change** suffices.
- Assume the current implementation is overengineered: try to delete
  before adding; every added line must directly serve a requirement.
- Merge functions/structures when their separation isn't justified;
  remove useless intermediate variables; reuse existing structures.
- No cosmetic refactoring — each modification must reduce size or
  complexity while preserving observable behavior.

## Before finishing

1. Run the relevant tests / compile checks.
2. Inspect the diff; actively remove anything not strictly necessary.
3. Report why each remaining changed file is needed.
4. For refactors: compare old/new line counts and state preserved
   behavior.
