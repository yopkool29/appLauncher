<p align="center">
  <img src="icon.png" width="128" alt="App Launcher logo">
</p>

# App Launcher

Desktop tool (Python + tkinter) to centralize start/stop of local apps:
each app groups one or more processes (bash commands, `docker compose`,
`docker run`...), with live status, detected ports and per-process logs.

<p align="center">
  <img src="docs/image.png" alt="App Launcher screenshot">
</p>

## Run

```bash
pip install -r requirements.txt   # PyYAML + psutil + sv_ttk + pystray + Pillow
python3 applauncher.py
```

The app catalog is loaded from the first existing location, in order:

1. `--config <path>` (command-line flag)
2. `$APPLAUNCHER_CONFIG` (environment variable)
3. `./apps.yaml`, next to `applauncher.py` (dev checkout)
4. `~/.config/applauncher/apps.yaml` (installed)

`apps.yaml` is your personal catalog — it is gitignored and never
pushed. `tkinter` ships with Python; `docker` and `tmux` support only
activates when those binaries are installed.

## Catalog (`apps.yaml`)

```yaml
apps:
  - name: "My app"
    url: "http://localhost:8000"        # optional (fallback browser target)
    processes:
      - name: "api"
        cmd: "python3 -m http.server 8000"
        workdir: "/tmp"                  # optional
        stop: ""                         # custom stop command (optional)
        ports: [8000]                    # expected ports (optional)
        docker: false                    # true if cmd spawns containers
        tmux: false                      # true to spawn inside a tmux session
```

Editable from the GUI (double-click / Edit button / right-click) or by
hand; saved back to the same file.

## Features

- **Start / stop / restart** per app (all its processes) or per process
  (toolbar toggle, context menu, keyboard-free double-click editing).
- **Process states**: running / partial / external (started outside the
  tool) / finished / stopped. Docker processes are matched against
  `docker ps` by compose `workdir` label **or** process name in the
  container name — no port probing needed.
- **tmux spawning** (`tmux: true`): one session per app (`al-<app>`);
  `tmux_window: N` selects the window index — processes sharing it
  become panes of that window (`tmux_layout` sets the split style).
  Attach via the context menu ("Attach") or `tmux attach -t al-<app>`.
  Panes survive launcher restarts and are re-adopted.
- **Ports**: auto-detected per process (PID tree via psutil) + docker
  host ports. Ports tab = whole machine, tagged with the owning app.
- **Logs**: per-process files in `logs/<app>/<proc>.log` (or `docker logs`
  for docker procs); clearable from the UI. Launcher diagnostics in
  `logs/applauncher.log` (rotating, English) — lifecycle, errors,
  shutdown trace. Path: `--logs-dir` flag (or `APPLAUNCHER_LOGS` env),
  else `./logs` in a dev checkout, else
  `$XDG_STATE_HOME/applauncher/logs` (installed).
- **Browser launch**: dropdown (Firefox/Brave, persisted). Per-process
  `browser_mode` (`window`/`tab`/`none` — 'none' hides its Open entries).
  App menu "Open URLs" and the Launch button open every app URL
  (`url`, `launch_port`, each process port) in its own mode; tabs are
  opened in the most recently used browser window (X11 stacking).
- **Themes**: Sun Valley dark/light + native ttk themes, switchable at
  runtime, persisted.
- **System tray**: minimize to tray, restore/quit from the icon menu.
  Hardened quit path (stop deadline + watchdogs) so the app always exits.

## Preferences

`~/.config/applauncher/prefs.json` — browser choice and theme.

## Notes / limits

- Foreground `docker run` without `--rm`/`stop`: killing the client may
  leave the container — declare `stop` or use `docker: true` (container
  stop via `docker ps` matching).
- Processes owned by other users may appear without PIDs (psutil perms).
- tmux sessions keep running after the launcher exits unless stopped.
