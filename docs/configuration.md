# Configuration reference

The app catalog is a YAML file resolved in this order:

1. `--config <path>` CLI flag
2. `APPLAUNCHER_CONFIG` env var
3. `./apps.yaml` next to the repo (development checkout)
4. `$XDG_CONFIG_HOME/applauncher/apps.yaml`
5. `~/.config/applauncher/apps.yaml`

See `docs/apps.example.yaml` for a full commented skeleton.

## App fields

| Field | Default | Description |
|---|---|---|
| `name` | — | Identity name (required): tree iids, tmux session, log dir. |
| `alias` | `""` | Display name shown in the tree/log header instead of `name`. |
| `url` | `""` | URL opened by *Open URLs* / *Launch*. |
| `launch_port` | `0` | Port waited on by *Launch* before opening URLs. |
| `tmux_layout` | `""` (=tiled) | Pane layout: `tiled`, `horizontal`, `vertical`. |
| `tmux_attach` | `true` | Open a terminal when a tmux process starts. |
| `tmux_shared` | `false` | Join the preceding group's session as a tab (see below). |
| `exclude_all` | `false` | Ignored by *Start all* / *Stop all* (name struck through). |
| `color` | `""` | Hex color (`#rrggbb`) of the square shown in the tree. |
| `browser` | `""` | Override the global browser for every process of this app (detected browser name, e.g. `"Chromium"`). Empty = toolbar selection. |
| `processes` | `[]` | List of processes (below). |

### tmux grouping (ordered)

App order drives tmux grouping:

- `tmux_shared: false` ("Join previous session" unchecked) starts a
  **new dedicated session** `al-<app>`.
- `tmux_shared: true` ("Join previous session" checked) joins the
  session of the closest preceding non-shared app that has tmux
  processes — as a named tab.
- `tmux_attach: false` ("Attach new terminal window" unchecked) runs
  panes without opening a terminal. Checked, a terminal opens only if
  the session has no client attached yet.

Only launcher-created sessions (tagged `@al-launcher`) are ever used or
attached — external tmux sessions are never adopted. Reordering apps is
locked while any process runs, because the order defines the groups.

## Process fields

| Field | Default | Description |
|---|---|---|
| `name` | — | Display name (required). |
| `cmd` | — | Shell command (required). Run through `sh -c`. Prefix env vars with `env` (`env FOO=1 cmd`), not bare `FOO=1` — the global command wrapper ends with `exec {cmd}`. |
| `workdir` | `""` | Working directory (`~` and `$VARS` expanded). |
| `stop` | `""` | Custom stop command; default kills the process group. `{port}` expands to the first declared port (or app `launch_port`). |
| `ports` | `[]` | Declared ports: Ports tab, status, browser open, wait-on-launch. |
| `docker` | `false` | Process spawns containers (status/logs via `docker ps`). |
| `tmux` | `false` | Run inside tmux instead of a detached Popen. |
| `tmux_window` | `0` | Window index inside the app session; same index = same window. |
| `browser_mode` | `window` | See below. |

### `browser_mode`

| Value | *Open URLs* (app) | *Open :port* / `u` / *Launch* (process) |
|---|---|---|
| `window` | yes, new window | new window |
| `tab` | yes, new tab | new tab in the most recent browser window |
| `manual` | **no** (its ports are blocked, incl. `url`/`launch_port` pointing at them) | new window |
| `none` | no | hidden — no Open entries |

`same_window: true` (legacy) migrates to `browser_mode: tab` on load.
URLs are deduplicated; an app-level `url`/`launch_port` pointing at a
`none`/`manual` process port is excluded too.

## Logs

- Every process writes to `<logs-dir>/<app>/<proc>.log`; docker
  processes show `docker logs` instead. The **Logs** tab has a sub-tab
  per process (auto-refresh) and *Clear* empties a log.
- The launcher's diagnostics go to `<logs-dir>/applauncher.log`
  (rotated, 2 MB × 5): starts/stops, `stop:` commands, config reloads,
  errors. *View > Launcher log* shows it live.
- Logs default to `logs/` next to the script (dev) or
  `$XDG_STATE_HOME/applauncher/logs`; `--logs-dir` or
  `APPLAUNCHER_LOGS` overrides.

## Shortcuts

`e` edit · `s` start/stop · `u` open in browser · `Del` delete ·
`F5` refresh · `Ctrl+,` preferences · `Ctrl+Q` quit
