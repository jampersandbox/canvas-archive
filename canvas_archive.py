#!/usr/bin/env python3
"""
canvas_archive.py
=================
One-click Canvas course archiver with a simple graphical interface.
Double-click the launcher created by setup_mac.sh / setup_windows.bat
— or run:  python canvas_archive.py
"""
import json
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

HERE = Path(__file__).parent.resolve()

# ──────────────────────────────  CONSTANTS  ───────────────────────────────────

COMMON_CANVAS_URLS = [
    "https://canvas.harvard.edu",
    "https://canvas.yale.edu",
    "https://canvas.mit.edu",
    "https://canvas.stanford.edu",
    "https://canvas.princeton.edu",
    "https://canvas.columbia.edu",
    "https://canvas.cornell.edu",
    "https://canvas.upenn.edu",
    "https://canvas.dartmouth.edu",
    "https://canvas.brown.edu",
    "https://canvas.uchicago.edu",
    "https://canvas.duke.edu",
    "https://canvas.northwestern.edu",
    "https://canvas.vanderbilt.edu",
    "https://canvas.emory.edu",
    "https://canvas.georgetown.edu",
    "https://canvas.bu.edu",
    "https://canvas.bc.edu",
    "https://canvas.tufts.edu",
    "https://canvas.nyu.edu",
    "https://canvas.usc.edu",
    "https://canvas.virginia.edu",
    "https://canvas.wustl.edu",
]

CONFIG_FILE = HERE / "canvas_config.json"

REQUIRED_SCRIPTS = [
    "canvas_auth.py",
    "canvas_downloader.py",
    "external_downloader.py",
    "panopto_downloader.py",
    "reserves_downloader.py",
]

# ──────────────────────────────  CONFIG  ──────────────────────────────────────

def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "canvas_url":    "https://canvas.harvard.edu",
        "panopto_url":   "https://harvard.hosted.panopto.com",
        "output_dir":    str(Path.home() / "Documents" / "canvas_downloads"),
        "skip_ongoing":  True,
        "skip_videos":   False,
        "do_canvas":     True,
        "do_external":   True,
        "do_panopto":    True,
        "do_reserves":   True,
    }


def save_config(cfg: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def write_canvas_config(canvas_url: str, panopto_url: str) -> None:
    """Write canvas_config.py so all scripts use the chosen URLs."""
    (HERE / "canvas_config.py").write_text(
        f"CANVAS_BASE_URL  = {canvas_url!r}\n"
        f"PANOPTO_BASE_URL = {panopto_url!r}\n",
        encoding="utf-8",
    )


# ──────────────────────────────  APP  ─────────────────────────────────────────

class CanvasArchiveApp:

    # ── Construction ──────────────────────────────────────────────────────────

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Canvas Archive  🎓")
        self.root.geometry("900x740")
        self.root.resizable(True, True)
        self.root.configure(bg="#f0f0f0")

        self._cfg = load_config()

        # State variables
        self.canvas_url   = tk.StringVar(value=self._cfg["canvas_url"])
        self.panopto_url  = tk.StringVar(
            value=self._cfg.get("panopto_url",
                                "https://harvard.hosted.panopto.com")
        )
        self.output_dir   = tk.StringVar(value=self._cfg["output_dir"])
        self.skip_ongoing = tk.BooleanVar(value=self._cfg["skip_ongoing"])
        self.skip_videos  = tk.BooleanVar(value=self._cfg["skip_videos"])
        self.do_canvas    = tk.BooleanVar(value=self._cfg["do_canvas"])
        self.do_external  = tk.BooleanVar(value=self._cfg["do_external"])
        self.do_panopto   = tk.BooleanVar(value=self._cfg["do_panopto"])
        self.do_reserves  = tk.BooleanVar(value=self._cfg["do_reserves"])

        # Runtime state
        self.running        = False
        self.process:       subprocess.Popen | None = None
        self.log_queue:     queue.Queue = queue.Queue()
        self.script_queue:  list[tuple[str, list[str]]] = []
        self._login_pending = False

        self._build_ui()
        self._poll_log()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # ── Header ────────────────────────────────────────────────────────────
        header = tk.Frame(self.root, bg="#4a148c", pady=18)
        header.pack(fill="x")

        tk.Label(
            header,
            text="🎓  Canvas Archive",
            font=("Helvetica", 24, "bold"),
            fg="white", bg="#4a148c",
        ).pack()

        tk.Label(
            header,
            text="Save all your course materials before you lose access",
            font=("Helvetica", 11),
            fg="#e1bee7", bg="#4a148c",
        ).pack(pady=(2, 0))

        # ── Main scrollable area ───────────────────────────────────────────────
        main = tk.Frame(self.root, bg="#f0f0f0", padx=24, pady=12)
        main.pack(fill="both", expand=True)

        # ── Settings ──────────────────────────────────────────────────────────
        sf = ttk.LabelFrame(main, text=" ⚙️  Settings ", padding=14)
        sf.pack(fill="x", pady=(0, 10))
        sf.configure()

        # Canvas URL row
        self._labeled_row(
            sf,
            label="Canvas URL:",
            widget_factory=lambda f: ttk.Combobox(
                f, textvariable=self.canvas_url,
                values=COMMON_CANVAS_URLS,
                width=48, font=("Helvetica", 11),
            ),
            hint="Select your school from the list, or type your own URL",
        )

        # Output directory row
        dir_frame = tk.Frame(sf, bg="white")
        dir_frame.pack(fill="x", pady=5)
        tk.Label(
            dir_frame, text="Save files to:", width=14,
            anchor="w", bg="white", font=("Helvetica", 11),
        ).pack(side="left")
        ttk.Entry(
            dir_frame, textvariable=self.output_dir,
            width=42, font=("Helvetica", 11),
        ).pack(side="left", padx=4)
        ttk.Button(
            dir_frame, text="Browse…",
            command=self._browse_dir,
        ).pack(side="left", padx=4)

        # ── What to download ──────────────────────────────────────────────────
        wf = ttk.LabelFrame(main, text=" 📥  What to download ", padding=14)
        wf.pack(fill="x", pady=(0, 10))

        items = [
            (self.do_canvas,
             "📄  Course files",
             "All PDFs, slides, videos, and documents uploaded to Canvas"),
            (self.do_external,
             "🔗  External readings",
             "JSTOR articles, Google Drive files, and other linked content"),
            (self.do_panopto,
             "🎬  Lecture recordings",
             "Panopto videos recorded by your professors"),
            (self.do_reserves,
             "📚  Library reserve readings",
             "Articles and book chapters on course reserve (Leganto/Alma)"),
        ]

        for var, label, desc in items:
            row = tk.Frame(wf, bg="white")
            row.pack(fill="x", pady=4)
            ttk.Checkbutton(row, text=label, variable=var).pack(side="left")
            tk.Label(
                row, text=f"  —  {desc}",
                fg="#666", bg="white", font=("Helvetica", 9),
            ).pack(side="left")

        # ── Options ───────────────────────────────────────────────────────────
        of = ttk.LabelFrame(main, text=" 🔧  Options ", padding=14)
        of.pack(fill="x", pady=(0, 10))

        opts = tk.Frame(of, bg="white")
        opts.pack(fill="x")
        ttk.Checkbutton(
            opts,
            text="Skip administrative / ongoing courses  ",
            variable=self.skip_ongoing,
        ).pack(side="left")
        ttk.Checkbutton(
            opts,
            text="Skip video files  (saves a lot of disk space)",
            variable=self.skip_videos,
        ).pack(side="left")

        # ── Log ───────────────────────────────────────────────────────────────
        lf = ttk.LabelFrame(main, text=" 📋  Progress log ", padding=8)
        lf.pack(fill="both", expand=True, pady=(0, 10))

        self.log_text = scrolledtext.ScrolledText(
            lf,
            height=11,
            font=("Courier", 10),
            bg="#1e1e1e", fg="#d4d4d4",
            insertbackground="white",
            state="disabled",
        )
        self.log_text.pack(fill="both", expand=True)

        for tag, colour in [
            ("success", "#4ec9b0"),
            ("error",   "#f44747"),
            ("warn",    "#dcdcaa"),
            ("info",    "#9cdcfe"),
            ("header",  "#c586c0"),
            ("dim",     "#888888"),
        ]:
            self.log_text.tag_config(tag, foreground=colour)

        # ── Bottom controls ───────────────────────────────────────────────────
        ctrl = tk.Frame(self.root, bg="#e8e8e8", pady=12, padx=24)
        ctrl.pack(fill="x")

        style = ttk.Style()
        style.configure(
            "Start.TButton",
            font=("Helvetica", 13, "bold"),
            padding=(16, 8),
        )

        self.start_btn = ttk.Button(
            ctrl,
            text="▶   Start Download",
            style="Start.TButton",
            command=self._start,
        )
        self.start_btn.pack(side="left", padx=(0, 12))

        self.stop_btn = ttk.Button(
            ctrl,
            text="⏹  Stop",
            command=self._stop,
            state="disabled",
        )
        self.stop_btn.pack(side="left")

        self.status_var = tk.StringVar(
            value="Ready — click 'Start Download' to begin."
        )
        tk.Label(
            ctrl,
            textvariable=self.status_var,
            fg="#444", bg="#e8e8e8",
            font=("Helvetica", 10),
        ).pack(side="left", padx=20)

    def _labeled_row(self, parent, label, widget_factory, hint=""):
        row = tk.Frame(parent, bg="white")
        row.pack(fill="x", pady=5)
        tk.Label(
            row, text=label, width=14,
            anchor="w", bg="white", font=("Helvetica", 11),
        ).pack(side="left")
        widget = widget_factory(row)
        widget.pack(side="left", padx=4)
        if hint:
            tk.Label(
                row, text=hint, fg="#888", bg="white",
                font=("Helvetica", 9),
            ).pack(side="left", padx=4)

    # ── Actions ───────────────────────────────────────────────────────────────

    def _browse_dir(self):
        d = filedialog.askdirectory(
            title="Choose where to save your files",
            initialdir=self.output_dir.get(),
        )
        if d:
            self.output_dir.set(d)

    def _start(self):
        if self.running:
            return

        canvas_url = self.canvas_url.get().strip().rstrip("/")
        if not canvas_url.startswith("http"):
            messagebox.showerror(
                "Invalid URL",
                "Please enter a valid Canvas URL starting with https://",
            )
            return

        # Save settings
        cfg = {
            "canvas_url":    canvas_url,
            "panopto_url":   self.panopto_url.get().strip().rstrip("/"),
            "output_dir":    self.output_dir.get().strip(),
            "skip_ongoing":  self.skip_ongoing.get(),
            "skip_videos":   self.skip_videos.get(),
            "do_canvas":     self.do_canvas.get(),
            "do_external":   self.do_external.get(),
            "do_panopto":    self.do_panopto.get(),
            "do_reserves":   self.do_reserves.get(),
        }
        save_config(cfg)
        write_canvas_config(cfg["canvas_url"], cfg["panopto_url"])

        # Build script queue
        out     = cfg["output_dir"]
        ongoing = ["--skip-ongoing"] if cfg["skip_ongoing"] else []
        novid   = ["--skip-videos"]  if cfg["skip_videos"]  else []

        self.script_queue = []
        if cfg["do_canvas"]:
            self.script_queue.append((
                "canvas_downloader.py",
                ["--dir", out] + ongoing + novid,
            ))
        if cfg["do_external"]:
            self.script_queue.append((
                "external_downloader.py",
                ["--dir", out],
            ))
        if cfg["do_panopto"]:
            self.script_queue.append((
                "panopto_downloader.py",
                ["--dir", out] + ongoing,
            ))
        if cfg["do_reserves"]:
            self.script_queue.append((
                "reserves_downloader.py",
                ["--dir", out] + ongoing,
            ))

        if not self.script_queue:
            messagebox.showwarning(
                "Nothing selected",
                "Please select at least one type of content to download.",
            )
            return

        # Create output directory
        Path(out).mkdir(parents=True, exist_ok=True)

        self.running = True
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self._login_pending = False
        self._clear_log()

        self._log("═" * 58 + "\n", "header")
        self._log("  🎓  Canvas Archive — Starting\n", "header")
        self._log(f"  📁  Saving to: {out}\n", "info")
        self._log("═" * 58 + "\n\n", "header")

        self._run_next_script()

    def _stop(self):
        self.running = False
        if self.process:
            try:
                self.process.terminate()
            except Exception:
                pass
            self.process = None
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.status_var.set("Stopped.")
        self._log("\n⏹  Download stopped.\n", "warn")

    def _run_next_script(self):
        if not self.running:
            return
        if not self.script_queue:
            self._all_done()
            return

        script_name, args = self.script_queue.pop(0)
        script_path = HERE / script_name

        if not script_path.exists():
            self._log(f"  ⚠  {script_name} not found — skipping.\n", "warn")
            self.root.after(200, self._run_next_script)
            return

        friendly = {
            "canvas_downloader.py":    "Downloading course files…",
            "external_downloader.py":  "Downloading external readings…",
            "panopto_downloader.py":   "Downloading lecture recordings…",
            "reserves_downloader.py":  "Downloading library reserves…",
        }.get(script_name, f"Running {script_name}…")

        self.status_var.set(friendly)
        self._log(f"\n{'─' * 58}\n", "dim")
        self._log(f"  ▶  {friendly}\n", "info")
        self._log(f"{'─' * 58}\n", "dim")

        try:
            self.process = subprocess.Popen(
                [sys.executable, str(script_path)] + args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE,
                bufsize=1,
                universal_newlines=True,
                cwd=str(HERE),
            )
        except Exception as exc:
            self._log(f"  ✗  Could not start {script_name}: {exc}\n", "error")
            self.root.after(500, self._run_next_script)
            return

        def _reader():
            for line in self.process.stdout:
                self.log_queue.put(("line", line))
            self.process.wait()
            self.log_queue.put(("done", self.process.returncode))

        threading.Thread(target=_reader, daemon=True).start()

    def _all_done(self):
        self.running = False
        self.process = None
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        out = self.output_dir.get()
        self.status_var.set(f"✅  All done!  Files saved to: {out}")
        self._log("\n" + "═" * 58 + "\n", "success")
        self._log("  ✅  All downloads complete!\n", "success")
        self._log(f"  📁  Files saved to:\n      {out}\n", "success")
        self._log("═" * 58 + "\n", "success")
        messagebox.showinfo(
            "Download Complete! 🎓",
            f"All done!\n\nYour files have been saved to:\n\n{out}\n\n"
            "You can open that folder now to see everything.",
            parent=self.root,
        )

    # ── Log ───────────────────────────────────────────────────────────────────

    def _clear_log(self):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")

    def _log(self, text: str, tag: str | None = None):
        self.log_text.config(state="normal")
        self.log_text.insert("end", text, (tag,) if tag else ())
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _poll_log(self):
        """Drain the log queue and handle login prompts (called every 100ms)."""
        try:
            while True:
                kind, data = self.log_queue.get_nowait()

                if kind == "line":
                    line = data
                    # Choose colour
                    if any(c in line for c in ("✓", "FINISHED", "Downloaded")):
                        tag = "success"
                    elif any(c in line for c in ("✗", "FAILED", "Error", "error")):
                        tag = "error"
                    elif any(c in line for c in ("⚠", "WARNING", "timed out",
                                                  "Waiting")):
                        tag = "warn"
                    elif any(c in line for c in ("═", "─", "📚", "📹",
                                                  "📖", "🎓", "🌐")):
                        tag = "header"
                    else:
                        tag = None
                    self._log(line, tag)

                    # Detect login prompt
                    if (
                        "Press ENTER" in line
                        or "press ENTER" in line.lower()
                        or "ENTER after you are logged in" in line
                        or "ENTER once signed in" in line
                    ):
                        if not self._login_pending:
                            self._login_pending = True
                            self.root.after(300, self._show_login_dialog)

                elif kind == "done":
                    rc = data
                    if rc == 0:
                        self._log(
                            "  ✓  Step complete.\n", "success"
                        )
                    else:
                        self._log(
                            f"  ⚠  Finished with exit code {rc}.\n", "warn"
                        )
                    self.root.after(600, self._run_next_script)

        except queue.Empty:
            pass

        self.root.after(100, self._poll_log)

    def _show_login_dialog(self):
        """
        Show a friendly dialog when a login prompt is detected.
        Sends Enter to the subprocess stdin when user clicks OK.
        """
        self._log(
            "\n  💬  Waiting for you to log in…\n", "warn"
        )
        messagebox.showinfo(
            "🔐  Please Log In",
            "A browser window has opened.\n\n"
            "Please log in with your university credentials "
            "as you normally would.\n\n"
            "Once you can see your Canvas dashboard "
            "(or the page you were trying to reach),\n"
            "click OK here to continue.",
            parent=self.root,
        )
        self._login_pending = False
        # Send Enter to unblock the input() call in the script
        if self.process and self.process.stdin:
            try:
                self.process.stdin.write("\n")
                self.process.stdin.flush()
            except Exception:
                pass
        self._log("  ✅  Login confirmed — continuing…\n\n", "success")


# ─────────────────────────────  STARTUP CHECKS  ───────────────────────────────

def check_setup() -> bool:
    """Return True if all required scripts are present."""
    missing = [s for s in REQUIRED_SCRIPTS if not (HERE / s).exists()]
    if missing:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Missing files",
            "Some required files are missing from this folder:\n\n"
            + "\n".join(f"  • {s}" for s in missing)
            + "\n\nPlease make sure all files from the Canvas Archive\n"
              "package are in the same folder as canvas_archive.py.",
        )
        root.destroy()
        return False
    return True


def check_packages() -> bool:
    """Return True if required Python packages are installed."""
    missing = []
    for pkg in ["requests", "playwright", "yt_dlp", "tqdm"]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg.replace("_", "-"))
    if missing:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Setup incomplete",
            "Some required packages are not installed.\n\n"
            "Please run the setup script for your platform:\n"
            "  • Mac:     bash setup_mac.sh\n"
            "  • Windows: setup_windows.bat\n\n"
            "Then try again.",
        )
        root.destroy()
        return False
    return True


# ──────────────────────────────────  MAIN  ────────────────────────────────────

def main():
    if not check_setup():
        sys.exit(1)
    if not check_packages():
        sys.exit(1)

    root = tk.Tk()

    # Centre the window on screen
    root.update_idletasks()
    w, h = 900, 740
    x = (root.winfo_screenwidth()  - w) // 2
    y = (root.winfo_screenheight() - h) // 2
    root.geometry(f"{w}x{h}+{x}+{y}")

    app = CanvasArchiveApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()