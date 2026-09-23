"""Integration tmux : session 'al-<app>', un pane par processus."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from .config import App, Process, safe_name

# APPLAUNCHER_NO_TMUX=1 : force le mode "sans tmux" (tests)
TMUX_OK = (
	shutil.which("tmux") is not None
	and os.getenv("APPLAUNCHER_NO_TMUX") != "1"
)

# layout tmux par app : (select-layout, split -h ?)
TMUX_LAYOUTS = {
	"tiled": ("tiled", False),                 # grille
	"horizontal": ("even-horizontal", True),   # panes cote a cote
	"vertical": ("even-vertical", False),      # panes empiles
}


SHARED_SESSION = "al"  # repli : apps shared sans leader avant elles
MARK = "@al-launcher"  # option posee a la creation : session "a nous"


def session_name(app: App, apps: Optional[List[App]] = None) -> str:
	"""Session tmux de l'app. Non-shared : sa session dediee
	'al-<app>' ("new window"). Shared : la session de l'app non-shared
	la plus proche AVANT elle dans l'ordre — les apps shared forment
	un groupe ordonne derriere chaque "new window" ; 'al' a defaut."""
	if not app.tmux_shared:
		return f"al-{safe_name(app.name)}"
	leader: Optional[App] = None
	for a in apps or []:
		if a is app or a.name == app.name:
			break
		if not a.tmux_shared and any(p.tmux for p in a.processes):
			leader = a
	return f"al-{safe_name(leader.name)}" if leader else SHARED_SESSION


def window_key(app: App, proc: Process) -> str:
	"""Fenetre tmux du proc : nommee d'apres l'app en session
	partagee (chaque app = un onglet), index numerique sinon."""
	if app.tmux_shared:
		key = safe_name(app.name)
		return f"{key}.{proc.tmux_window}" if proc.tmux_window else key
	return str(proc.tmux_window)


def tgt(session: str) -> str:
	"""Cible tmux exacte : '=' empeche le prefix-matching
	('al' matcherait sinon 'al-data-download')."""
	return f"={session}"


def _run(*args: str) -> subprocess.CompletedProcess:
	return subprocess.run(
		["tmux", *args],
		stdout=subprocess.DEVNULL,
		stderr=subprocess.DEVNULL,
		timeout=10,
	)


def _out(*args: str) -> str:
	try:
		out = subprocess.run(
			["tmux", *args], capture_output=True, text=True, timeout=5
		)
	except (OSError, subprocess.TimeoutExpired):
		return ""
	return out.stdout.strip() if out.returncode == 0 else ""


def session_exists(session: str) -> bool:
	"""Session existante ET creee par le launcher — une session
	exterieure du meme nom n'est jamais adoptee/attachee."""
	try:
		if _run("has-session", "-t", tgt(session)).returncode != 0:
			return False
		# NB: display-message ne resout pas les @options avec '=sess'
		# -> nom nu, sur car l'existence exacte vient d'etre verifiee
		return _out(
			"display-message", "-p", "-t", session, f"#{{{MARK}}}"
		) == "1"
	except (OSError, subprocess.TimeoutExpired):
		return False


def session_attached(session: str) -> bool:
	"""Au moins un client tmux (terminal) est attache a NOTRE session."""
	if not session_exists(session):
		return False
	out = _out(
		"display-message", "-p", "-t", session,
		"#{session_attached}"
	)
	return out.isdigit() and int(out) > 0


def _pane(f: List[str]) -> dict:
	"""Parse une ligne list-panes : id, proc, app, pid, dead, pipe."""
	try:
		pid = int(f[3] or 0)
	except ValueError:
		pid = 0
	return {
		"id": f[0], "proc": f[1], "app": f[2], "pid": pid,
		"dead": f[4] == "1", "pipe": f[5] == "1",
	}


def list_panes(session: str) -> List[dict]:
	"""Panes de la session : id, tags @al-proc/@al-app, pid, dead, pipe."""
	out = _out(
		"list-panes", "-s", "-t", tgt(session),
		"-F",
		"#{pane_id}\t#{@al-proc}\t#{@al-app}\t#{pane_pid}"
		"\t#{pane_dead}\t#{pane_pipe}",
	)
	return [
		_pane(line.split("\t"))
		for line in out.splitlines()
		if len(line.split("\t")) == 6
	]


def find_pane(
	session: str, app_name: str, proc_name: str
) -> Optional[dict]:
	"""Pane d'un proc : tag @al-proc + @al-app (ce dernier absent
	sur les panes crees avant le tag -> "" accepte)."""
	for pane in list_panes(session):
		if pane["proc"] == proc_name and pane["app"] in ("", app_name):
			return pane
	return None


def owned_sessions() -> List[str]:
	"""Sessions marquee @al-launcher (creees par l'app, jamais
	une session exterieure du meme nom)."""
	out = _out(
		"list-sessions", "-F", f"#{{session_name}}\t#{{{MARK}}}"
	)
	return [
		line.split("\t")[0]
		for line in out.splitlines()
		if line.endswith("\t1")
	]


def find_pane_anywhere(app_name: str, proc_name: str) -> Optional[dict]:
	"""Pane taggee dans N'IMPORTE quelle session a nous — robuste
	aux reordonnances : le pane reste ou il a ete cree."""
	owned = set(owned_sessions())
	if not owned:
		return None
	out = _out(
		"list-panes", "-a", "-F",
		"#{session_name}\t#{pane_id}\t#{@al-proc}\t#{@al-app}"
		"\t#{pane_pid}\t#{pane_dead}\t#{pane_pipe}",
	)
	for line in out.splitlines():
		p = line.split("\t")
		if len(p) != 7 or p[0] not in owned:
			continue
		if p[2] == proc_name and p[3] in ("", app_name):
			return {**_pane(p[1:]), "session": p[0]}
	return None


def _windows(session: str) -> List[Tuple[str, str, str]]:
	"""(index, nom, @al-app) de chaque fenetre de la session."""
	out = _out(
		"list-windows", "-t", tgt(session),
		"-F", "#{window_index}\t#{window_name}\t#{@al-app}",
	)
	wins = []
	for line in out.splitlines():
		idx, _, rest = line.partition("\t")
		name, _, app = rest.partition("\t")
		wins.append((idx, name, app))
	return wins


def arm_pipe(path: Path, pane_id: str) -> None:
	"""Arme pipe-pane sur un pane adopte sans pipe (sinon log vide) ;
	-o = no-op si un pipe est deja actif. Backfill l'historique du
	pane dans le log si celui-ci est vide."""
	try:
		path.parent.mkdir(parents=True, exist_ok=True)
		_run("pipe-pane", "-o", "-t", pane_id, f"cat >> {path}")
		if path.stat().st_size == 0:
			out = _out(
				"capture-pane", "-p", "-e", "-S", "-", "-t", pane_id
			)
			if out:
				path.write_text(out + "\n", encoding="utf-8")
	except (OSError, subprocess.TimeoutExpired):
		pass


def kill_pane(pane_id: str) -> None:
	"""Tue le pane meme mort (remain-on-exit) ; la session disparait
	quand le dernier pane est tue."""
	try:
		_run("kill-pane", "-t", pane_id)
	except (OSError, subprocess.TimeoutExpired):
		pass


def _win_opts(t: str, app_name: str) -> List[str]:
	"""Options posees sur chaque fenetre creee : pane mort visible,
	status bar avec le nom seul (#W#F = nom + flag courant), tag
	@al-app (distingue les fenetres d'apps differentes dans un
	groupe : l'index seul ne suffit plus)."""
	return [
		";", "set-option", "-t", t, "remain-on-exit", "on",
		";", "set-option", "-t", t, "@al-app", app_name,
		";", "set-option", "-t", t, "window-status-format", " #W#F ",
		";", "set-option", "-t", t, "window-status-current-format",
		" #W#F ",
	]


def retile(session: str, window: str, layout_key: str) -> None:
	layout = TMUX_LAYOUTS.get(layout_key, TMUX_LAYOUTS["tiled"])[0]
	try:
		_run("select-layout", "-t", f"{tgt(session)}:{window}", layout)
	except (OSError, subprocess.TimeoutExpired):
		pass


def spawn(
	app: App, proc: Process, cmd: str, log_path: Path, session: str
) -> Tuple[str, int]:
	"""Lance cmd dans un pane tmux tagge @al-proc=<proc.name>
	(fenetre = proc.tmux_window), dans la session resolue par le
	Caller via session_name(app, apps). Retourne (pane_id, pane_pid) ;
	leve RuntimeError en cas d'echec."""
	st = tgt(session)  # cible exacte, evite le prefix-matching
	layout, horiz = TMUX_LAYOUTS.get(
		app.tmux_layout, TMUX_LAYOUTS["tiled"]
	)
	pipe = f"cat >> {log_path}"
	win = window_key(app, proc)
	target = f"{st}:{win}"

	def _pipe_tags(t: str) -> List[str]:
		"""Queue commune : cmd, pipe de log et tags @al-* sur la cible."""
		return [
			cmd, ";", "pipe-pane", "-t", t, "-o", pipe,
			";", "set-option", "-p", "-t", t, "@al-proc", proc.name,
			";", "set-option", "-p", "-t", t, "@al-app", app.name,
		]

	pane_id = ""
	pane = find_pane(session, app.name, proc.name)
	if pane is not None:
		# pane taggee morte -> relance en place, layout conserve
		args = (
			["respawn-pane", "-k", "-t", pane["id"], cmd,
			 ";", "pipe-pane", "-t", pane["id"], "-o", pipe]
		)
		pane_id = pane["id"]
	elif session_exists(session):
		wins = _windows(session)
		if any(
			win in (i, n) and a in ("", app.name) for i, n, a in wins
		):
			# fenetre existante -> nouveau pane dedans
			# (sans -d : devient pane courant de la fenetre ->
			# pipe-pane et tag s'y appliquent, atomique)
			args = (
				["split-window", "-P", "-F", "#{pane_id}", "-t", target]
				+ (["-h"] if horiz else [])
				+ _pipe_tags(target)
				+ [";", "select-layout", "-t", target, layout]
			)
		else:
			# nouvelle fenetre : onglet nomme (shared) ou index
			# demande, sinon premier index libre (groupe : l'index
			# peut etre pris par un membre si le leader arrive apres)
			t = target
			if not app.tmux_shared:
				idxs = {int(i) for i, _, _ in wins if i.isdigit()}
				if int(win) in idxs:
					free = next(
						i for i in range(len(idxs) + 1)
						if i not in idxs
					)
					t = f"{st}:{free}"
			args = (
				["new-window", "-d", "-P", "-F", "#{pane_id}",
				 "-t", f"{st}:" if app.tmux_shared else t,
				 # nom = l'app (sinon auto-rename = la commande)
				 "-n", safe_name(app.name)]
				+ _pipe_tags(t) + _win_opts(t, app.name)
			)
	else:
		args = (
			["new-session", "-d", "-s", session,
			 "-x", "220", "-y", "50",
			 "-n", safe_name(app.name),
			 "-P", "-F", "#{pane_id}"]
			+ _pipe_tags(f"{st}:0")
			+ [";", "set-option", "-t", session, MARK, "1",
			   ";", "set-option", "-t", session,
			   "status-left-length", "40"]
			# session dediee : le nom de l'app est deja sur l'onglet,
			# pas de [al-app] redondant a gauche ; une session de
			# groupe garde [al-<leader>] pour identifier le groupe
			+ ([
				";", "set-option", "-t", session, "status-left", ""
			] if not app.tmux_shared else [])
			+ _win_opts(f"{st}:0", app.name)
		)
		if not app.tmux_shared and proc.tmux_window:
			args += [
				";", "move-window", "-s", f"{st}:0", "-t", target,
			]
	res = subprocess.run(
		["tmux", *args], capture_output=True, text=True, timeout=10
	)
	if res.returncode != 0:
		raise RuntimeError(res.stderr.strip() or "tmux failed")
	pane_id = pane_id or res.stdout.strip()
	pane_pid = int(
		_out("display-message", "-p", "-t", pane_id, "#{pane_pid}") or 0
	)
	return pane_id, pane_pid
