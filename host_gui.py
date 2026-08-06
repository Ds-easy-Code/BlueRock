#!/usr/bin/env python3
"""
BlueRock host launcher (CustomTkinter edition).

Runs on your HOST machine (not inside any container). It:
  1. Lets you pick one or MORE files via a file dialog. All are queued into
     ./inbox/queue -- the Streamlit app on the other side loads only ONE of
     them into memory at a time (whichever the queue's "current" index
     points at), so a folder full of files never gets loaded all at once.
  2. Runs `podman compose up -d --build`, then polls the app URL until it's
     actually answering before declaring itself "Running" and opening a
     browser tab. Use the Prev/Next buttons inside the app's sidebar to
     step through the queue one file at a time.
  3. Stop button runs `podman compose down`.
  4. Destination-folder picker + Move button moves the file CURRENTLY BEING
     VIEWED (read from the shared state file) from its original host path
     to a destination folder, then drops it out of the queue.
  5. Podman machine Start/Stop/Status buttons for convenience.

Place this file in the same directory as compose.yaml (the BlueRock repo
root) or point REPO_DIR below at that directory.

Requirements:
    pip install customtkinter filelock
Podman + the podman-compose / `podman compose` plugin must be installed
and on PATH.

Presetting the source/destination folders:
    Copy .env.example to .env (same folder as this file) and fill in
    BLUEROCK_SOURCE_DIR / BLUEROCK_DEST_DIR. .env is loaded automatically
    on startup -- see load_env_file() below. Real environment variables
    (e.g. set in your shell) always take priority over .env.
"""

import logging
import logging.handlers
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from core.queue_state import read_index, write_index

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
REPO_DIR = Path(__file__).resolve().parent      # directory containing compose.yaml
INBOX_DIR = REPO_DIR / "inbox"                  # bind-mounted into the container
QUEUE_DIR = INBOX_DIR / "queue"                 # individual files live here
STATE_FILE = INBOX_DIR / "state.json"           # {"index": N} -- shared with the app
APP_URL = "http://localhost:8501"
CONTAINER_NAME = "bluerock"  # must match container_name in compose.yaml


def load_env_file(path: Path) -> None:
    """Load simple KEY=VALUE pairs from an .env file into os.environ.

    Deliberately hand-rolled instead of depending on python-dotenv --
    host_gui.py already asks people to `pip install` a couple of GUI-only
    packages by hand (it isn't installed via requirements.txt, which is
    for the container), so this avoids adding a third. Only sets a key if
    it isn't already present in the real environment, so `FOO=bar python
    host_gui.py` (or anything your shell/OS already exports) still wins
    over whatever is in .env.
    """
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_env_file(REPO_DIR / ".env")

# Folder the file picker opens into by default -- the place you actually
# browse/pick files FROM. Files are still copied into QUEUE_DIR above; this
# does not change where the container mounts, only where the dialog starts.
# Configurable via BLUEROCK_SOURCE_DIR (in your shell env or in .env, see
# .env.example) so this isn't hardcoded to one person's machine; falls back
# to the user's home directory.
SOURCE_DIR = os.environ.get("BLUEROCK_SOURCE_DIR") or str(Path.home())

# Folder the "Move current file to destination" Browse dialog opens into
# and preloads the destination field with, so you don't have to navigate
# there by hand every launch. Configurable via BLUEROCK_DEST_DIR (in your
# shell env or in .env). Falls back to empty, same as before.
DEST_DIR = os.environ.get("BLUEROCK_DEST_DIR") or ""

COMPOSE_UP_CMD = ["podman", "compose", "up", "-d", "--build"]
COMPOSE_DOWN_CMD = ["podman", "compose", "down"]
MACHINE_START_CMD = ["podman", "machine", "start"]
MACHINE_STOP_CMD = ["podman", "machine", "stop"]
MACHINE_LIST_CMD = ["podman", "machine", "list"]

# Long-running commands get killed if they run past this -- without a
# timeout, a hung `podman compose up` (or a hung Podman machine) left the
# GUI with no recovery path short of killing the process externally.
COMPOSE_TIMEOUT_SECONDS = 300  # image build can legitimately take a while
MACHINE_CMD_TIMEOUT_SECONDS = 90

# `podman machine start` returning success only means the WSL/HyperKit VM
# booted -- there's a brief window right after where the client's local
# socket/pipe relay to that VM isn't wired up yet. Running a podman command
# immediately (e.g. `compose up`) in that window fails with something like
# "dial tcp 127.0.0.1:PORT: ...actively refused it". Poll a cheap command
# until it actually succeeds before doing anything real.
PODMAN_SOCKET_WAIT_TIMEOUT_SECONDS = 45
PODMAN_SOCKET_POLL_INTERVAL_SECONDS = 2

# After `podman compose up` returns, poll the app URL until it actually
# responds (or this timeout elapses) before declaring "Running" -- the
# container process exiting 0 doesn't mean Streamlit has finished booting.
READY_TIMEOUT_SECONDS = int(os.environ.get("BLUEROCK_READY_TIMEOUT_SECONDS", "150"))
READY_POLL_INTERVAL_SECONDS = 1.5
# How often to log a "still waiting" heartbeat while polling for readiness,
# so a slow-but-working startup doesn't look identical to a hung one in the
# log -- see _wait_until_ready.
READY_HEARTBEAT_SECONDS = 15

# ---------------------------------------------------------------------------
# File logging (in addition to the in-app log box, which is lost on close)
# ---------------------------------------------------------------------------
_logger = logging.getLogger("bluerock.launcher")
_logger.setLevel(logging.INFO)
_log_handler = logging.handlers.RotatingFileHandler(
    REPO_DIR / "launcher.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8",
)
_log_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
_logger.addHandler(_log_handler)

# ---------------------------------------------------------------------------
# Look & feel
# ---------------------------------------------------------------------------
DANGER = "#e5534b"
DANGER_HOVER = "#c3453e"
OK = "#3fb950"
IDLE = "gray60"
WARN = "#e3b341"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class BlueRockLauncher(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("BlueRock Launcher")
        self.geometry("680x680")
        self.minsize(620, 560)

        # name -> original host Path, for files currently queued
        self.file_map: dict[str, Path] = {}
        self.container_running = False

        self._build_ui()
        QUEUE_DIR.mkdir(parents=True, exist_ok=True)

        # Reflect the Podman machine's real state on open instead of always
        # starting the label at "Idle" regardless of what's actually running.
        self.after(150, self.on_machine_status)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _section(self, title, subtitle=None, expand=False):
        card = ctk.CTkFrame(self, corner_radius=12)
        card.pack(fill="x", padx=16, pady=(0, 12), expand=expand)
        if expand:
            card.pack_configure(fill="both")

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both" if expand else "x", expand=expand, padx=16, pady=14)

        ctk.CTkLabel(inner, text=title, font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w")
        if subtitle:
            ctk.CTkLabel(
                inner, text=subtitle, font=ctk.CTkFont(size=11),
                text_color="gray60", wraplength=580, justify="left",
            ).pack(anchor="w", pady=(2, 8))
        return inner

    def _build_ui(self):
        ctk.CTkLabel(
            self, text="\U0001F6E1  BlueRock Launcher", font=ctk.CTkFont(size=20, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(16, 12))

        # --- Podman machine controls ---
        body = self._section("Podman machine", "Start/stop the Podman VM directly, or check what's running.")
        row0 = ctk.CTkFrame(body, fg_color="transparent")
        row0.pack(fill="x")
        self.machine_start_btn = ctk.CTkButton(row0, text="Start Machine", width=130, command=self.on_machine_start)
        self.machine_start_btn.pack(side="left")
        self.machine_stop_btn = ctk.CTkButton(row0, text="Stop Machine", width=130, command=self.on_machine_stop)
        self.machine_stop_btn.pack(side="left", padx=8)
        self.machine_status_btn = ctk.CTkButton(row0, text="Machine Status", width=130, command=self.on_machine_status)
        self.machine_status_btn.pack(side="left")

        # --- File picker / launch ---
        body = self._section(
            "Pick file(s) to open",
            "Files are queued and shown one at a time in the app -- use Prev/Next there to step through.",
        )
        self.queue_list = ctk.CTkTextbox(body, height=100, font=("Consolas", 11))
        self.queue_list.pack(fill="x", pady=(0, 10))
        self.queue_list.configure(state="disabled")

        row1 = ctk.CTkFrame(body, fg_color="transparent")
        row1.pack(fill="x")
        self.pick_btn = ctk.CTkButton(row1, text="Choose File(s) & Launch", command=self.on_pick_and_launch)
        self.pick_btn.pack(side="left")

        self.stop_btn = ctk.CTkButton(
            row1, text="Stop", command=self.on_stop, state="disabled",
            fg_color=DANGER, hover_color=DANGER_HOVER,
        )
        self.stop_btn.pack(side="left", padx=8)

        self.open_btn = ctk.CTkButton(
            row1, text="Open in Browser", command=self.on_open_browser, state="disabled",
        )
        self.open_btn.pack(side="left", padx=(0, 8))

        self.status_label = ctk.CTkLabel(row1, text="\u25CF Idle", text_color=IDLE)
        self.status_label.pack(side="left", padx=8)

        # --- Move file ---
        body = self._section(
            "Move the file currently being viewed",
            "Uses whichever file the app's Prev/Next currently points at.",
        )
        row2 = ctk.CTkFrame(body, fg_color="transparent")
        row2.pack(fill="x")
        self.dest_var = ctk.StringVar(value=DEST_DIR)
        ctk.CTkEntry(row2, textvariable=self.dest_var).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(row2, text="Browse...", width=90, command=self.on_browse_dest).pack(side="left", padx=(8, 0))

        row3 = ctk.CTkFrame(body, fg_color="transparent")
        row3.pack(fill="x", pady=(10, 0))
        self.move_btn = ctk.CTkButton(
            row3, text="Move Current File to Destination", command=self.on_move, state="disabled",
        )
        self.move_btn.pack(side="left")

        # --- Log ---
        body = self._section("Log", expand=True)
        self.log = ctk.CTkTextbox(body, font=("Consolas", 11))
        self.log.pack(fill="both", expand=True)
        self.log.configure(state="disabled")

    # ------------------------------------------------------------------
    # Thread-safe UI helpers
    #
    # Tkinter widgets may only be touched from the main thread. Several
    # operations here (compose up/down, machine commands, moving a file)
    # run on background threads so the GUI doesn't freeze; every widget
    # mutation those threads need is routed through self.after(0, ...) so
    # it actually executes on the main thread instead of corrupting/crashing
    # the UI. These helpers are safe to call from any thread.
    # ------------------------------------------------------------------
    def _set_status(self, text: str, color: str):
        self.after(0, lambda: self.status_label.configure(text=f"\u25CF {text}", text_color=color))

    def log_line(self, text: str):
        text = text.rstrip()
        if not text:
            return
        _logger.info(text)

        def _append():
            self.log.configure(state="normal")
            self.log.insert("end", text + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")

        self.after(0, _append)

    def _set_widget_state(self, widget, state: str):
        self.after(0, lambda: widget.configure(state=state))

    def _refresh_queue_listbox(self):
        def _do():
            self.queue_list.configure(state="normal")
            self.queue_list.delete("1.0", "end")
            names = sorted(self.file_map.keys())
            if names:
                self.queue_list.insert("end", "\n".join(f"\u2022 {n}" for n in names))
            else:
                self.queue_list.insert("end", "(nothing queued yet)")
            self.queue_list.configure(state="disabled")

        self.after(0, _do)

    def _show_error(self, title: str, message: str):
        self.after(0, lambda: messagebox.showerror(title, message))

    def _show_warning(self, title: str, message: str):
        self.after(0, lambda: messagebox.showwarning(title, message))

    def _ask_yes_no(self, title: str, message: str, on_result):
        """messagebox.askyesno blocks, so it must run on the main thread;
        `on_result` is called back with the boolean answer."""
        def _do():
            on_result(messagebox.askyesno(title, message))
        self.after(0, _do)

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------
    def _run_logged(self, cmd: list[str], label: str, timeout: float | None = None):
        """Run a command, streaming its output into the log. Runs on
        whatever thread calls it -- callers should already be off the main
        thread if the command might take a while. If `timeout` elapses
        before the process finishes, it's killed rather than left to hang
        the launcher indefinitely."""
        killed_by_timeout = False
        proc = None
        timer = None
        try:
            self.log_line(f"Running: {' '.join(cmd)}")
            proc = subprocess.Popen(
                cmd, cwd=REPO_DIR,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
            )
            if timeout:
                def _kill_on_timeout():
                    nonlocal killed_by_timeout
                    killed_by_timeout = True
                    self.log_line(f"{label} timed out after {timeout:.0f}s -- killing it.")
                    proc.kill()
                timer = threading.Timer(timeout, _kill_on_timeout)
                timer.start()

            for line in proc.stdout:
                self.log_line(line)
            proc.wait()

            if killed_by_timeout:
                return None
            if proc.returncode != 0:
                self.log_line(f"{label} exited with code {proc.returncode}")
            else:
                self.log_line(f"{label} finished OK.")
            return proc.returncode
        except FileNotFoundError as e:
            self.log_line(f"Error: {e}")
            self._show_error("Not found", f"Command not found: {e}. Is podman installed and on PATH?")
            return None
        except Exception as e:  # noqa: BLE001
            self.log_line(f"Error: {e}")
            self._show_error("Error", str(e))
            return None
        finally:
            if timer:
                timer.cancel()

    def _wait_until_ready(self, url: str, timeout: float) -> bool:
        """Poll `url` until it responds or `timeout` elapses. The container
        exiting `podman compose up` with code 0 only means the process
        launched -- Streamlit inside it can still take several seconds (or,
        with a larger image like after adding ffmpeg, sometimes well over a
        minute on a resource-constrained VM) to finish booting, during
        which the URL isn't reachable yet.

        Logs a heartbeat every READY_HEARTBEAT_SECONDS so a slow-but-working
        startup is visibly still progressing in the log, rather than going
        silent for the full timeout and looking indistinguishable from
        being stuck.
        """
        start = time.monotonic()
        deadline = start + timeout
        last_heartbeat = start
        while time.monotonic() < deadline:
            try:
                urllib.request.urlopen(url, timeout=2)
                return True
            except (urllib.error.URLError, OSError):
                now = time.monotonic()
                if now - last_heartbeat >= READY_HEARTBEAT_SECONDS:
                    self.log_line(
                        f"Still waiting for {url} ({now - start:.0f}s elapsed)... "
                        f"container is up, app inside hasn't answered yet."
                    )
                    last_heartbeat = now
                time.sleep(READY_POLL_INTERVAL_SECONDS)
        return False

    def _log_container_diagnostics(self, tail: int = 60) -> None:
        """Pull recent logs from the container and write them to the log --
        called when the app fails to come up in time, so the *actual*
        reason (a Python traceback, an OOM kill, a slow import, etc.) is
        visible instead of just "didn't respond". This is the single most
        useful thing to look at when this happens, since the container
        itself already reported starting successfully at this point."""
        self.log_line(f"Fetching the last {tail} lines of container logs for diagnosis...")
        try:
            result = subprocess.run(
                ["podman", "logs", "--tail", str(tail), CONTAINER_NAME],
                cwd=REPO_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                timeout=15, text=True,
            )
            output = result.stdout.strip()
            if output:
                self.log_line("----- container logs -----")
                for line in output.splitlines():
                    self.log_line(line)
                self.log_line("----- end container logs -----")
            else:
                self.log_line("(no container log output captured)")
        except Exception as e:  # noqa: BLE001
            self.log_line(f"Couldn't fetch container logs: {e}")

        # Also record whether the container is still alive at all -- if it
        # already exited, the app almost certainly crashed on startup
        # rather than just being slow, which changes what to look for.
        try:
            result = subprocess.run(
                ["podman", "inspect", "-f", "{{.State.Status}}", CONTAINER_NAME],
                cwd=REPO_DIR, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=10, text=True,
            )
            status = result.stdout.strip()
            if status:
                self.log_line(f"Container status: {status}"
                               + ("  (it exited -- check the traceback above)" if status != "running" else ""))
        except Exception:
            pass

    def _wait_for_podman_socket(self, timeout: float) -> bool:
        """Poll `podman info` until the client can actually reach the
        machine's API socket, rather than assuming it's ready the instant
        `podman machine start` (or an already-running machine) is in play.
        See PODMAN_SOCKET_WAIT_TIMEOUT_SECONDS above for why this exists."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                result = subprocess.run(
                    ["podman", "info"], cwd=REPO_DIR,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=10,
                )
                if result.returncode == 0:
                    return True
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                pass
            time.sleep(PODMAN_SOCKET_POLL_INTERVAL_SECONDS)
        return False

    # ------------------------------------------------------------------
    # Podman machine controls
    # ------------------------------------------------------------------
    def _run_machine_cmd(self, cmd, label, button):
        self._set_widget_state(button, "disabled")
        try:
            self._run_logged(cmd, label, timeout=MACHINE_CMD_TIMEOUT_SECONDS)
        finally:
            self._set_widget_state(button, "normal")

    def on_machine_start(self):
        threading.Thread(
            target=self._run_machine_cmd,
            args=(MACHINE_START_CMD, "podman machine start", self.machine_start_btn),
            daemon=True,
        ).start()

    def on_machine_stop(self):
        threading.Thread(
            target=self._run_machine_cmd,
            args=(MACHINE_STOP_CMD, "podman machine stop", self.machine_stop_btn),
            daemon=True,
        ).start()

    def on_machine_status(self):
        threading.Thread(
            target=self._run_machine_cmd,
            args=(MACHINE_LIST_CMD, "podman machine list", self.machine_status_btn),
            daemon=True,
        ).start()

    # ------------------------------------------------------------------
    # File pick + launch
    # ------------------------------------------------------------------
    def on_open_browser(self):
        webbrowser.open(APP_URL)

    # File-picker "supported types" filter. Duplicated here (rather than
    # imported from core.archive / core.video) on purpose: those modules
    # import Pillow/OpenCV, which this host-side script has no reason to
    # require -- host_gui.py's own dependencies are just customtkinter and
    # filelock. Keep this list in sync with IMAGE_EXTS (core/archive.py)
    # and VIDEO_EXTS (core/video.py) if either changes.
    _SUPPORTED_EXTS = (
        "*.zip",
        "*.png", "*.jpg", "*.jpeg", "*.webp", "*.gif", "*.bmp", "*.tiff",
        "*.mp4", "*.mov", "*.avi", "*.mkv", "*.webm", "*.m4v",
    )

    def on_pick_and_launch(self):
        # Reuse whatever folder was picked from last time this session,
        # so browsing several batches in a row doesn't keep bouncing back
        # to BLUEROCK_SOURCE_DIR every single time.
        initialdir = getattr(self, "_last_source_dir", None) or SOURCE_DIR
        paths = filedialog.askopenfilenames(
            title="Select file(s) to open in BlueRock",
            initialdir=initialdir,
            filetypes=[
                ("Supported media", " ".join(self._SUPPORTED_EXTS)),
                ("All files", "*.*"),
            ],
        )
        if not paths:
            return

        self._last_source_dir = str(Path(paths[0]).resolve().parent)
        self.pick_btn.configure(state="disabled")
        self._set_status("Starting...", WARN)
        threading.Thread(target=self._launch_worker, args=(list(paths),), daemon=True).start()

    def _launch_worker(self, paths: list[str]):
        try:
            # Clear anything stale, then queue every picked file individually.
            for old in QUEUE_DIR.glob("*"):
                if old.is_file():
                    old.unlink()
            self.file_map.clear()

            for p in paths:
                src = Path(p)
                dest = QUEUE_DIR / src.name
                if dest.exists():
                    # avoid collisions between two picked files with the same name
                    dest = QUEUE_DIR / f"{src.stem}__{len(self.file_map)}{src.suffix}"
                shutil.copy2(src, dest)
                self.file_map[dest.name] = src
                self.log_line(f"Queued: {src} -> {dest}")

            write_index(STATE_FILE, 0)
            self._refresh_queue_listbox()

            if not self.container_running:
                # Start the machine ourselves instead of requiring the user to
                # run `podman machine start` in a terminal first. This is safe
                # to run even if the machine is already up: podman then just
                # exits non-zero with an "already running" message, which we
                # don't treat as fatal -- the real gate is the reachability
                # check right after. On native Linux (rootless podman, no
                # machine concept) this command simply fails harmlessly and
                # the reachability check below passes immediately anyway.
                self.log_line("Starting Podman machine (if needed)...")
                self._run_logged(MACHINE_START_CMD, "podman machine start", timeout=MACHINE_CMD_TIMEOUT_SECONDS)

                self.log_line("Checking that Podman is reachable...")
                if not self._wait_for_podman_socket(PODMAN_SOCKET_WAIT_TIMEOUT_SECONDS):
                    self.log_line(
                        f"Podman still isn't reachable after {PODMAN_SOCKET_WAIT_TIMEOUT_SECONDS:.0f}s."
                    )
                    self._set_status("Failed", DANGER)
                    self._show_error(
                        "Podman not ready",
                        "Could not connect to Podman after trying to start the "
                        "machine.\n\nCheck Machine Status for details, or try "
                        "again in a few seconds -- WSL machines can occasionally "
                        "take longer than usual to finish booting.",
                    )
                    self._set_widget_state(self.pick_btn, "normal")
                    return

                # Clean up any stale container left over from a crash, a manual
                # `podman compose up` outside this GUI, or the GUI being closed
                # without clicking Stop -- otherwise `up` fails with a name clash.
                self.log_line(f"Removing any stale '{CONTAINER_NAME}' container (if present)...")
                subprocess.run(
                    ["podman", "rm", "-f", CONTAINER_NAME],
                    cwd=REPO_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=MACHINE_CMD_TIMEOUT_SECONDS,
                )

                returncode = self._run_logged(COMPOSE_UP_CMD, "podman compose up", timeout=COMPOSE_TIMEOUT_SECONDS)

                if returncode != 0:
                    self._set_status("Failed", DANGER)
                    self._show_error("Podman error", "podman compose up failed (or timed out). Check the log.")
                    self._set_widget_state(self.pick_btn, "normal")
                    return

                self.container_running = True
                self.log_line(f"Container command finished -- waiting for {APP_URL} to respond...")
                self._set_status("Waiting for app...", WARN)

                if self._wait_until_ready(APP_URL, READY_TIMEOUT_SECONDS):
                    self.log_line(f"App is up at {APP_URL}.")
                else:
                    self.log_line(
                        f"App didn't respond within {READY_TIMEOUT_SECONDS:.0f}s. "
                        f"It may still be starting -- try opening {APP_URL} manually."
                    )
                    self._log_container_diagnostics()
            else:
                self.log_line("Container already running -- queue updated. "
                               "Click into the app and use Prev/Next, or refresh the page.")

            self._set_status("Running", OK)
            self._set_widget_state(self.stop_btn, "normal")
            self._set_widget_state(self.open_btn, "normal")
            self._set_widget_state(self.move_btn, "normal")
            self._set_widget_state(self.pick_btn, "normal")
        except FileNotFoundError as e:
            self.log_line(f"Error: {e}")
            self._show_error("Not found", f"Command not found: {e}. Is podman installed and on PATH?")
            self._set_widget_state(self.pick_btn, "normal")
            self._set_status("Idle", IDLE)
        except Exception as e:  # noqa: BLE001
            self.log_line(f"Error: {e}")
            self._show_error("Error", str(e))
            self._set_widget_state(self.pick_btn, "normal")
            self._set_status("Idle", IDLE)

    # ------------------------------------------------------------------
    # Stop
    # ------------------------------------------------------------------
    def on_stop(self):
        self.stop_btn.configure(state="disabled")
        self._set_status("Stopping...", WARN)
        threading.Thread(target=self._stop_worker, daemon=True).start()

    def _stop_worker(self):
        try:
            self._run_logged(COMPOSE_DOWN_CMD, "podman compose down", timeout=COMPOSE_TIMEOUT_SECONDS)
            subprocess.run(
                ["podman", "rm", "-f", CONTAINER_NAME],
                cwd=REPO_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=MACHINE_CMD_TIMEOUT_SECONDS,
            )
            self.container_running = False
            self._set_status("Stopped", IDLE)
            self._set_widget_state(self.pick_btn, "normal")
            self._set_widget_state(self.open_btn, "disabled")
            self.log_line("Container stopped.")
        except Exception as e:  # noqa: BLE001
            self.log_line(f"Error: {e}")
            self._show_error("Error", str(e))
            self._set_widget_state(self.stop_btn, "normal")

    # ------------------------------------------------------------------
    # Move current file
    # ------------------------------------------------------------------
    def on_browse_dest(self):
        # Start from whatever's currently in the field (typically the
        # BLUEROCK_DEST_DIR preset) if it's a real directory, so re-browsing
        # doesn't dump you back at some OS default every time.
        current = self.dest_var.get().strip()
        initialdir = current if current and Path(current).is_dir() else (DEST_DIR or str(Path.home()))
        folder = filedialog.askdirectory(title="Select destination folder", initialdir=initialdir)
        if folder:
            self.dest_var.set(folder)

    def on_move(self):
        dest_dir = self.dest_var.get().strip()
        if not dest_dir:
            messagebox.showwarning("No destination", "Choose a destination folder first.")
            return
        dest_dir_path = Path(dest_dir)
        if not dest_dir_path.is_dir():
            messagebox.showerror("Invalid destination", f"Not a directory: {dest_dir_path}")
            return

        # Moving can be slow (a large file across drives is a copy+delete),
        # so this runs off the main thread -- otherwise the whole GUI would
        # freeze for the duration. The button is disabled meanwhile so a
        # second click can't start an overlapping move.
        self.move_btn.configure(state="disabled")
        threading.Thread(target=self._move_worker, args=(dest_dir_path,), daemon=True).start()

    def _move_worker(self, dest_dir_path: Path):
        try:
            queue = sorted(
                (f for f in QUEUE_DIR.iterdir() if f.is_file() and not f.name.startswith(".")),
                key=lambda p: p.name,
            )
            if not queue:
                self._show_warning("Nothing queued", "There's no file currently queued.")
                return

            idx = read_index(STATE_FILE, len(queue))
            current = queue[idx]
            original = self.file_map.get(current.name)
            if not original or not original.exists():
                self._show_error("Missing file", f"Original source file no longer exists for: {current.name}")
                return

            target = dest_dir_path / original.name
            if target.exists():
                proceed = threading.Event()
                answer = {"yes": False}

                def _on_answer(yes):
                    answer["yes"] = yes
                    proceed.set()

                self._ask_yes_no("Overwrite?", f"{target} already exists. Overwrite?", _on_answer)
                proceed.wait()
                if not answer["yes"]:
                    return

            shutil.move(str(original), str(target))
            current.unlink(missing_ok=True)
            del self.file_map[current.name]
            self._refresh_queue_listbox()

            remaining = max(len(queue) - 1, 0)
            new_idx = min(idx, max(remaining - 1, 0))
            write_index(STATE_FILE, new_idx)

            self.log_line(f"Moved {original} -> {target}")
            self.log_line("Removed from queue. Refresh the app (or click Prev/Next) to see the update.")
            if not self.file_map:
                self._set_widget_state(self.move_btn, "disabled")
                return
        except Exception as e:  # noqa: BLE001
            self.log_line(f"Move failed: {e}")
            self._show_error("Move failed", str(e))
        finally:
            if self.file_map:
                self._set_widget_state(self.move_btn, "normal")


def main():
    app = BlueRockLauncher()
    app.mainloop()


if __name__ == "__main__":
    main()
