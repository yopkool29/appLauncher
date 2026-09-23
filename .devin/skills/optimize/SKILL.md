Refactor this code to make it much smaller and simpler, while preserving exactly its observable behavior.

Primary goal: sharply reduce the amount of code and complexity, not merely improve its style.

Constraints:

Aggressively look for lines that can be deleted.
Remove useless abstractions, wrappers, helpers, classes, interfaces and levels of indirection that bring no real value.
Merge functions or structures when their separation is not justified.
Eliminate unnecessary intermediate variables.
Reuse existing structures and functions directly whenever possible.
Avoid any generalization aimed at hypothetical needs.
Do not add any new abstraction without demonstrable necessity.
Reduce the number of modified files if possible.
Also reduce conceptual complexity, not only the line count.
Do not keep code merely because it is "more explicit" if a much simpler version remains readable.
Do not perform cosmetic refactoring: every change must contribute to simplifying the code.

Proceed in two phases:

Analysis
Identify the main sources of verbosity.
Identify the abstractions and indirections that can disappear.
Determine what can be merged or removed.
Reduction
Rewrite the code with a minimum-necessary-code strategy.
After the first refactoring pass, do a second pass specifically dedicated to finding even more code to delete.

At the end:

compare the old and new line counts;
list the removed abstractions;
list the preserved behaviors;
run the existing tests;
if some part of the code could not be reduced, briefly explain why.

Do not aim to produce an elegant architecture. Aim to produce the smallest reasonably maintainable implementation that satisfies the current requirements.
