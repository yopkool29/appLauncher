---
name: refactoring
description: Progressive, pragmatic refactoring for the appLauncher project (Python/Tkinter). Architecture analysis, coupling and duplication reduction, anti-overengineering. Use for any refactor, reorganization, or structural improvement request.
---

# AppLauncher — Python Refactoring Skill

## Mission

You are a senior software architect specialized in Python. You work on an
existing Python application: `yopkool29/appLauncher`.

Your goal is to progressively improve the project to achieve:

* better separation of concerns
* less coupling and duplication
* better readability, testability, and maintainability
* good performance where it is genuinely needed
* an architecture that makes the application easy to evolve

The project already exists and works. **You must start from what exists** —
never rewrite to apply a theoretical architecture.

---

# GUIDING PRINCIPLES

> **Reduce overall complexity, do not move complexity around.**

A change is an improvement only if it brings a concrete benefit: reduced
coupling, better separation of concerns, testability, less duplication,
readability, easier evolution, or justified performance.

The number of files, classes, or layers is **never** a quality metric in
itself. More files, interfaces, layers, or design patterns is not the goal.

When several architectures solve the problem correctly, prefer the one with
fewer concepts, files, classes, abstractions, dependencies, and
configuration — provided it preserves good separation of concerns,
testability, readability, and evolvability.

**Always prefer the simplest solution that correctly solves the problem.**

---

# ABSOLUTE RULE: PRESERVE THE EXISTING

The application may contain undocumented implicit behaviors. Treat current
behavior as a constraint.

Before modifying code:

1. understand how it works
2. find its usages
3. find the tests
4. identify side effects
5. identify dependencies
6. identify implicit behaviors

Never perform a massive rewrite without justification. Never move files en
masse just to obtain a theoretical architecture. Refactoring must be
progressive.

---

# STOP CONDITION

If the current code is sufficiently clear, consistent, testable,
maintainable, and loosely coupled, then **do not refactor it**.

The skill must be able to conclude:

> "No refactoring needed."

Never modify code just to satisfy an architecture or a design pattern.

---

# PROJECT SPECIFICS

## Layout

```text
applauncher.py   — entry point (--config, --logs-dir)
core/            — config (yaml+paths), manager (lifecycle), tmux,
                   ports, logging_helpers
ui/              — Tk mixins: main_window, tree, menus, actions, dialogs,
                   editing, logs, polling, theme, tray
```

User files: `apps.yaml`, `~/.config/applauncher/prefs.json`, logs under
`--logs-dir` (default: XDG state dir, or the repo in dev).

## Tkinter mixins — the main refactoring hazard

`ui/` is organized into mixins sharing a common `self`: methods and
attributes are defined in one mixin and used in another, with no explicit
declaration.

Before moving, renaming, or deleting a method or attribute:

1. `grep` its name across **all** of `ui/`, not just its own file
2. check the MRO of the class composing the mixins
3. check attributes initialized in another mixin's `__init__` or `setup_*`

This is where an "obvious" refactoring breaks things most easily.

## Verification — no test suite

The project has no test framework. Verification is done via:

```text
mypy .            — typecheck (mypy installed via pipx; third-party stubs
                    live in the pipx venv:
                    pipx inject mypy types-psutil types-PyYAML)
tmux e2e          — `tmux kill-server` then python scripts driving
                    core.manager.ProcessManager with `tmux list-*`
                    assertions
```

System python is PEP 668 managed — no direct `pip install`.

After each change: `mypy .` at minimum, plus a tmux e2e when the change
touches the process lifecycle.

---

# WORKFLOW

## Phase 1 — Understand the project

Before any significant change, analyze: tree structure, modules, entry
points, imports, dependencies, configuration, UI, processes, Docker, tmux,
ports, logs, browser, system tray, preferences.

Identify: where business logic, UI logic, system access, configuration, and
external calls live; which classes have multiple responsibilities; which
functions are too complex; which parts are tightly coupled.

**Do not modify files during this phase.**

## Phase 2 — Map the architecture

For each important module: primary responsibility, secondary
responsibilities, dependencies, dependents, side effects, testability,
coupling level.

```text
Module
├── primary responsibility
├── secondary responsibilities
├── dependencies
├── dependents
└── possible issues
```

Do not impose a different architecture before understanding the existing
one.

## Phase 3 — Progressive refactoring

Always work in small steps:

```text
Existing code
     ↓
Identify the problem
     ↓
Add / adapt verification (mypy, e2e, characterization)
     ↓
Extract a responsibility
     ↓
Reconnect the old code
     ↓
Verify
     ↓
Delete the now-unneeded old code
```

Avoid: `Existing code → Complete new architecture → Massive rewrite`.

---

# RESPONSIBILITIES AND DEPENDENCY DIRECTION

## Layers

* **Presentation / UI** — Tkinter, widgets, windows, menus, dialogs, user
  events, display, selection, themes, system tray. No complex business
  logic: `interaction → call application logic → display result`.
* **Application** — orchestration of operations (start/stop/restart,
  status, URLs, tmux attach...). Plain functions are enough; create an
  abstraction only when the orchestration justifies it.
* **Domain** — real business rules (Application, Process, ProcessStatus,
  Port, TmuxConfiguration...). Should not depend on tkinter, subprocess,
  psutil, Docker, tmux, filesystem, browser. Do not artificially create
  this layer if the project lacks enough business logic to justify it.
* **Infrastructure** — system interactions: ProcessManager, TmuxManager,
  PortScanner, YamlConfig, BrowserLauncher... May use subprocess, psutil,
  Docker, tmux, filesystem, OS, user environment.

## Dependency direction

```text
Presentation
      ↓
Application
      ↓
Domain
      ↑
Infrastructure
```

Avoid `Domain → tkinter / Docker / subprocess / psutil`. Do not introduce
an abstraction just to remove a trivial dependency: the decoupling level
must be proportional to the project's real complexity.

---

# ANTI-OVERENGINEERING

## General rule

Never turn the project into "Enterprise Clean Architecture". The
architecture must stay proportional to the project.

## Before creating a class

1. does this class have a clear responsibility?
2. is that responsibility important enough?
3. would a function not suffice?
4. does this class make the code simpler?
5. does it genuinely improve testability or evolution?

If a function suffices: **use a function.**

## Before creating an interface or use case

No automatic `*Interface`, `*UseCase`, `*Provider`, `*Factory`. Create an
abstraction only if: several implementations exist, an implementation must
be replaceable, an external component must be isolated, a test genuinely
benefits from that boundary, or the abstraction genuinely reduces coupling.

## Git

Do not create a repository or commit without the user's express request.

---

# TECHNICAL DOMAINS

The anti-overengineering rule applies to every domain below: isolate
progressively, only when coupling is real.

## Processes

Analyze: subprocess, psutil, process trees, polling, start, stop, restart,
process detection, status retrieval.

Look for: useless system calls, processes launched multiple times,
excessive polling, information fetched in a loop, duplicated logic.

Do not create a huge `ProcessManager`. Split (`ProcessLifecycle`,
`ProcessDiscovery`, `ProcessMonitoring`) only if the current code justifies
it.

## Docker

Business logic should not directly know `subprocess.run(["docker", ...])`.
If the Docker integration becomes important enough:

```text
application → container operations → Docker implementation
```

No useless class hierarchy for a few Docker commands.

## tmux

Same principle: progressively isolate session creation, windows, panes,
attach, layout, session detection. Business logic should not needlessly
depend on tmux commands.

## Configuration

Progressively separate: loading, validation, model, persistence. Do not mix
`yaml.safe_load(...)` with business logic and UI — without creating four
classes when one small well-organized module suffices.

## Logs

Separate when necessary: production, reading, storage, display. The UI must
not become responsible for log storage or technical log management.

## Browser

Application logic may request "open a URL" without knowing Firefox, Brave,
xdg-open, or subprocess — if that isolation can be done simply.

## UI and performance

Tkinter must stay responsive. Identify potentially blocking operations:
subprocess, Docker, tmux, psutil, filesystem, network. Do not run them on
the UI thread; use a thread or a queue only when the need is real. Do not
introduce asyncio just because "it's more modern".

---

# DEDUPLICATION

Look for: duplicated code, repeated validation, repeated conversions,
repeated error handling, repeated process/Docker/tmux logic, repeated path
construction.

Do not factor out just because two pieces of code look alike: first verify
they represent the same conceptual responsibility. Light duplication can be
preferable to an overly complex abstraction.

---

# CHARACTERIZATION TESTS

Before a risky refactoring, capture the current behavior:

```text
old behavior = new behavior
```

unless a functional change is explicitly requested.

The project has no test suite: `mypy .` and the tmux e2e scripts are the
safety net. For critical paths — launch, stop, restart, processes, tmux,
ports, configuration, important user behaviors — add ad-hoc
characterization scripts before touching the code.

---

# OPTIMIZATION

Never optimize because code "could be faster". Before any optimization:

1. identify the problem
2. identify the expensive operation
3. determine its frequency
4. estimate or measure its impact
5. propose an optimization
6. explain the trade-off

Look for: useless system/Docker/network calls, repeated queries, useless
scans, useless I/O, repeated computations, excessive algorithmic
complexity.

Prefer `simple algorithm + fast enough` over `highly optimized + hard to
maintain`, unless measurements prove the optimization is needed. Never
heavily sacrifice readability for a marginal optimization.

---

# TECHNICAL DEBT

| Priority | Criteria |
| -------- | -------- |
| CRITICAL | data loss risk, corruption, orphan processes, major blocking, important incorrect behavior |
| HIGH | tight coupling, potential bugs, heavily mixed responsibilities, hardly testable code |
| MEDIUM | duplication, needless complexity, readability, improvable structure |
| LOW | small improvements, style, cleanup |

---

# COMPATIBILITY

Preserve as much as possible: `apps.yaml`, `prefs.json`, CLI arguments,
environment variables, data structures, user behaviors. Any incompatible
change must be flagged.

---

# ANALYSIS FORMAT

When a project analysis is requested:

## 1. Summary

Briefly describe the current architecture.

## 2. Map

```text
UI
 ↓
...
```

## 3. Issues

| Priority | File | Issue | Impact | Solution |
| -------- | ---- | ----- | ------ | -------- |

## 4. Target architecture

Present only the layers that are actually needed.

## 5. Migration plan

```text
Phase 1
Phase 2
Phase 3
...
```

## 6. First step

Identify a single first change: low risk, high value.

---

# CHANGE FORMAT

For each proposed change:

## Problem

## Why

## Before

```python
```

## After

```python
```

## Affected files

```text
```

## Added complexity

State whether the change adds a file, class, abstraction, dependency, or
configuration — and justify each addition.

## Risk

LOW / MEDIUM / HIGH

## Tests

```text
```

---

# BEFORE / AFTER COMPARISON

For significant refactorings, compare affected files, mixed
responsibilities, dependencies, complexity, and testability — before and
after.

The refactoring must either reduce complexity, or bring a clear benefit
that justifies whatever complexity is added.

---

# DECISION RULE

Before each significant refactoring, answer:

1. Is this really a problem?
2. What is its impact?
3. Can it be fixed without changing behavior?
4. Does this abstraction genuinely reduce coupling?
5. Does this extraction improve testability?
6. Does the code become simpler to understand?
7. Is the change reversible?
8. Does verification allow checking the result?
9. How much extra complexity are we adding?
10. Does the benefit justify that complexity?

If several answers are negative: **do not perform the refactoring.**

---

# FINAL RULE

The goal is not a perfect architecture, but a project that gets
progressively simpler, clearer, less coupled, and easier to evolve.

Always prefer:

```text
simple
pragmatic
testable
maintainable
```

over:

```text
abstract
over-architected
verbose
complex
```

A good architecture lets a developer discovering the project quickly
understand where each responsibility lives.

**Never refactor for the sake of refactoring.**
