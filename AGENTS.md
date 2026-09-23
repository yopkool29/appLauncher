# App Launcher — dev notes

## Verification

- Typecheck: `mypy .` (mypy installed via pipx).
- Third-party stubs live in mypy's pipx venv, NOT the project env:
  `pipx inject mypy types-psutil types-PyYAML`
- System python is PEP 668 managed — plain `pip install` fails; use
  `pipx`/`apt`/`--break-system-packages` (last resort).
- tmux e2e: `tmux kill-server` then python scripts driving
  `core.manager.ProcessManager` + `tmux list-*` assertions.
- Skill check (`.devin/skills/<name>`): `uvx --from
  "git+https://github.com/agentskills/agentskills@main#subdirectory=skills-ref"
  skills-ref validate <dir>`

## Layout

- `applauncher.py` — entry point (`--config`, `--logs-dir`)
- `core/` — config (yaml+paths+prefs), manager (lifecycle), tmux,
  ports, browser, logging_helpers
- `ui/` — Tk mixins: main_window, tree, menus, actions, dialogs,
  editing, logs, polling, theme, tray, lifecycle
- User files: config `apps.yaml`, `~/.config/applauncher/prefs.json`,
  logs `--logs-dir` (default XDG state dir / repo in dev)
