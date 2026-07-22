#!/usr/bin/env python3
"""
BlueRock host launcher (CustomTkinter edition).

Runs on your HOST machine (not inside any container). It:
  1. Lets you pick one or MORE files via a file dialog. All are queued into
     ./inbox/queue -- the Streamlit app on the other side loads only ONE of
     them into memory at a time (whichever the queue's "current" index
     points at), so a folder full of files never gets loaded all at once.
  2. Runs `podman compose up -d --build`. Use the Prev/Next buttons inside
     the app's sidebar to step through the queue one file at a time.
  3. Stop button runs `podman compose down`.
  4. Destination-folder picker + Move button moves the file CURRENTLY BEING
     VIEWED (read from the shared state file) from its original host path
     to a destination folder, then drops it out of the queue.
  5. Podman machine Start/Stop/Status buttons for convenience.

Place this file in the same directory as compose.yaml (the BlueRock repo
root) or point REPO_DIR below at that directory.

Requirements:
    pip install customtkinter
Podman + the podman-compose / `podman compose` plugin must be installed
and on PATH.
"""

import json
import shutil
import subprocess
import threading
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
REPO_DIR = Path(__file__).resolve().parent      # directory containing compose.yaml
INBOX_DIR = REPO_DIR / "inbox"                  # bind-mounted into the container
QUEUE_DIR = INBOX_DIR / "queue"                 # individual files live here
STATE_FILE = INBOX_DIR / "state.json"           # {"index": N} -- shared with the app
APP_URL = "http://localhost:8501"
CONTAINER_NAME = "bluerock"  # must match container_name in compose.yaml

# Folder the file picker opens into by default -- the place you actually
# browse/pick files FROM. Files are still copied into QUEUE_DIR above; this
# does not change where the container mounts, only where the dialog starts.
SOURCE_DIR = r"D:\Softwares\Steam"

COMPOSE_UP_CMD = ["podman", "compose", "up", "-d", "--build"]
COMPOSE_DOWN_CMD = ["podman", "compose", "down"]
MACHINE_START_CMD = ["podman", "machine", "start"]
MACHINE_STOP_CMD = ["podman", "machine", "stop"]
MACHINE_LIST_CMD = ["podman", "machine", "list"]

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
        ctk.CTkButton(row0, text="Start Machine", width=130, command=self.on_machine_start).pack(side="left")
        ctk.CTkButton(row0, text="Stop Machine", width=130, command=self.on_machine_stop).pack(side="left", padx=8)
        ctk.CTkButton(row0, text="Machine Status", width=130, command=self.on_machine_status).pack(side="left")

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

        self.status_label = ctk.CTkLabel(row1, text="\u25CF Idle", text_color=IDLE)
        self.status_label.pack(side="left", padx=8)

        # --- Move file ---
        body = self._section(
            "Move the file currently being viewed",
            "Uses whichever file the app's Prev/Next currently points at.",
        )
        row2 = ctk.CTkFrame(body, fg_color="transparent")
        row2.pack(fill="x")
        self.dest_var = ctk.StringVar(value="")
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

    def _set_status(self, text: str, color: str):
        self.status_label.configure(text=f"\u25CF {text}", text_color=color)

    def log_line(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _refresh_queue_listbox(self):
        self.queue_list.configure(state="normal")
        self.queue_list.delete("1.0", "end")
        names = sorted(self.file_map.keys())
        if names:
            self.queue_list.insert("end", "\n".join(f"\u2022 {n}" for n in names))
        else:
            self.queue_list.insert("end", "(nothing queued yet)")
        self.queue_list.configure(state="disabled")

    # ------------------------------------------------------------------
    # Podman machine controls
    # ------------------------------------------------------------------
    def _run_logged(self, cmd: list[str], label: str):
        """Run a command, streaming its output into the log box. Runs on
        whatever thread calls it -- callers should already be off the main
        thread if the command might take a while."""
        try:
            self.log_line(f"Running: {' '.join(cmd)}")
            proc = subprocess.Popen(
                cmd, cwd=REPO_DIR,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            for line in proc.stdout:
                self.log_line(line)
            proc.wait()
            if proc.returncode != 0:
                self.log_line(f"{label} exited with code {proc.returncode}")
            else:
                self.log_line(f"{label} finished OK.")
            return proc.returncode
        except FileNotFoundError as e:
            self.log_line(f"Error: {e}")
            messagebox.showerror("Not found", f"Command not found: {e}. Is podman installed and on PATH?")
            return None
        except Exception as e:  # noqa: BLE001
            self.log_line(f"Error: {e}")
            messagebox.showerror("Error", str(e))
            return None

    def on_machine_start(self):
        threading.Thread(target=lambda: self._run_logged(MACHINE_START_CMD, "podman machine start"), daemon=True).start()

    def on_machine_stop(self):
        threading.Thread(target=lambda: self._run_logged(MACHINE_STOP_CMD, "podman machine stop"), daemon=True).start()

    def on_machine_status(self):
        threading.Thread(target=lambda: self._run_logged(MACHINE_LIST_CMD, "podman machine list"), daemon=True).start()

    # ------------------------------------------------------------------
    # File pick + launch
    # ------------------------------------------------------------------
    def on_pick_and_launch(self):
        paths = filedialog.askopenfilenames(
            title="Select file(s) to open in BlueRock",
            initialdir=SOURCE_DIR,
        )
        if not paths:
            return

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

            STATE_FILE.write_text(json.dumps({"index": 0}))
            self.after(0, self._refresh_queue_listbox)

            if not self.container_running:
                # Clean up any stale container left over from a crash, a manual
                # `podman compose up` outside this GUI, or the GUI being closed
                # without clicking Stop -- otherwise `up` fails with a name clash.
                self.log_line(f"Removing any stale '{CONTAINER_NAME}' container (if present)...")
                subprocess.run(
                    ["podman", "rm", "-f", CONTAINER_NAME],
                    cwd=REPO_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )

                self.log_line("Running: " + " ".join(COMPOSE_UP_CMD))
                proc = subprocess.Popen(
                    COMPOSE_UP_CMD, cwd=REPO_DIR,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                )
                for line in proc.stdout:
                    self.log_line(line)
                proc.wait()

                if proc.returncode != 0:
                    self._set_status("Failed", DANGER)
                    self.log_line(f"podman compose up exited with code {proc.returncode}")
                    messagebox.showerror("Podman error", "podman compose up failed. Check the log.")
                    self.pick_btn.configure(state="normal")
                    return

                self.container_running = True
                self.log_line(f"Container up. Open {APP_URL} in your browser when ready.")
            else:
                self.log_line("Container already running -- queue updated. "
                               "Click into the app and use Prev/Next, or refresh the page.")

            self._set_status("Running", OK)
            self.stop_btn.configure(state="normal")
            self.move_btn.configure(state="normal")
            self.pick_btn.configure(state="normal")
        except FileNotFoundError as e:
            self.log_line(f"Error: {e}")
            messagebox.showerror("Not found", f"Command not found: {e}. Is podman installed and on PATH?")
            self.pick_btn.configure(state="normal")
            self._set_status("Idle", IDLE)
        except Exception as e:  # noqa: BLE001
            self.log_line(f"Error: {e}")
            messagebox.showerror("Error", str(e))
            self.pick_btn.configure(state="normal")
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
            self.log_line("Running: " + " ".join(COMPOSE_DOWN_CMD))
            proc = subprocess.Popen(
                COMPOSE_DOWN_CMD, cwd=REPO_DIR,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            for line in proc.stdout:
                self.log_line(line)
            proc.wait()
            subprocess.run(
                ["podman", "rm", "-f", CONTAINER_NAME],
                cwd=REPO_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            self.container_running = False
            self._set_status("Stopped", IDLE)
            self.pick_btn.configure(state="normal")
            self.log_line("Container stopped.")
        except Exception as e:  # noqa: BLE001
            self.log_line(f"Error: {e}")
            messagebox.showerror("Error", str(e))
            self.stop_btn.configure(state="normal")

    # ------------------------------------------------------------------
    # Move current file
    # ------------------------------------------------------------------
    def on_browse_dest(self):
        folder = filedialog.askdirectory(title="Select destination folder")
        if folder:
            self.dest_var.set(folder)

    def _current_queue_index(self, n: int) -> int:
        if not STATE_FILE.exists():
            return 0
        try:
            idx = json.loads(STATE_FILE.read_text()).get("index", 0)
        except Exception:
            idx = 0
        return max(0, min(idx, max(n - 1, 0)))

    def on_move(self):
        dest_dir = self.dest_var.get().strip()
        if not dest_dir:
            messagebox.showwarning("No destination", "Choose a destination folder first.")
            return
        dest_dir_path = Path(dest_dir)
        if not dest_dir_path.is_dir():
            messagebox.showerror("Invalid destination", f"Not a directory: {dest_dir_path}")
            return

        queue = sorted(
            (f for f in QUEUE_DIR.iterdir() if f.is_file() and not f.name.startswith(".")),
            key=lambda p: p.name,
        )
        if not queue:
            messagebox.showwarning("Nothing queued", "There's no file currently queued.")
            return

        idx = self._current_queue_index(len(queue))
        current = queue[idx]
        original = self.file_map.get(current.name)
        if not original or not original.exists():
            messagebox.showerror("Missing file", f"Original source file no longer exists for: {current.name}")
            return

        target = dest_dir_path / original.name
        if target.exists():
            if not messagebox.askyesno("Overwrite?", f"{target} already exists. Overwrite?"):
                return

        try:
            shutil.move(str(original), str(target))
            current.unlink(missing_ok=True)
            del self.file_map[current.name]
            self._refresh_queue_listbox()

            remaining = max(len(queue) - 1, 0)
            new_idx = min(idx, max(remaining - 1, 0))
            STATE_FILE.write_text(json.dumps({"index": new_idx}))

            self.log_line(f"Moved {original} -> {target}")
            self.log_line("Removed from queue. Refresh the app (or click Prev/Next) to see the update.")
            if not self.file_map:
                self.move_btn.configure(state="disabled")
        except Exception as e:  # noqa: BLE001
            self.log_line(f"Move failed: {e}")
            messagebox.showerror("Move failed", str(e))


def main():
    app = BlueRockLauncher()
    app.mainloop()


if __name__ == "__main__":
    main()