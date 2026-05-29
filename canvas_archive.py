#!/usr/bin/env python3
"""
canvas_archive.py
=================
One-click Canvas course archiver with a simple graphical interface.
"""
import json
import os
import queue
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

CONFIG_FILE   = HERE / "canvas_config.json"
SENTINEL_FILE = HERE / "gui_login_ready.txt"

REQUIRED_SCRIPTS = [
    "canvas_auth.py",
    "canvas_downloader.py",
    "external_downloader.py",
    "panopto_downloader.py",
    "reserves_downloader.py",
]

_LOGIN_PHRASES = [
    "Press ENTER",
    "press ENTER",
    "press Enter",
    "Press Enter",
    "ENTER after you are logged in",
    "ENTER once signed in",
    "ENTER once you",
    "[Press ENTER",
    "come back here and press",
    "Waiting for GUI login",
    "Canvas Login Required",
    "Login Required",
    "Login required",
]


# ──────────────────────────────  CONFIG  ──────────────────────────────────────

def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "canvas_url":   "https://canvas.harvard.edu",
        "panopto_url":  "https://harvard.hosted.panopto.com",
        "output_dir":   str(Path.home() / "Documents" / "canvas_downloads"),
        "skip_ongoing": True,
        "skip_videos":  False,
        "do_canvas":    True,
        "do_external":  True,
        "do_panopto":   True,
        "do_reserves":  True,
    }


def save_config(cfg: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def write_canvas_config(canvas_url: str, panopto_url: str) -> None:
    (HERE / "canvas_config.py").write_text(
        f"CANVAS_BASE_URL  = {canvas_url!r}\n"
        f"PANOPTO_BASE_URL = {panopto_url!r}\n",
        encoding="utf-8",
    )


# ──────────────────────────────  APP  ─────────────────────────────────────────

class CanvasArchiveApp:

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Canvas Archive  🎓")
        self.root.geometry("920x800")
        self.root.resizable(True, True)
        self.root.configure(bg="#f0f0f0")

        self._cfg = load_config()

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

        self.running            = False
        self.process:           subprocess.Popen | None = None
        self.log_queue:         queue.Queue = queue.Queue()
        self.script_queue:      list[tuple[str, list[str]]] = []
        self._login_bar_visible = False
        self._dot_job:          str | None = None   # for animated dots

        if SENTINEL_FILE.exists():
            SENTINEL_FILE.unlink()

        self._build_ui()
        self._poll_log()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Header
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

        self.main = tk.Frame(self.root, bg="#f0f0f0", padx=24, pady=12)
        self.main.pack(fill="both", expand=True)

        # Settings
        sf = ttk.LabelFrame(self.main, text=" ⚙️  Settings ", padding=14)
        sf.pack(fill="x", pady=(0, 10))

        url_row = tk.Frame(sf, bg="white")
        url_row.pack(fill="x", pady=5)
        tk.Label(
            url_row, text="Canvas URL:", width=14,
            anchor="w", bg="white", font=("Helvetica", 11),
        ).pack(side="left")
        ttk.Combobox(
            url_row, textvariable=self.canvas_url,
            values=COMMON_CANVAS_URLS,
            width=48, font=("Helvetica", 11),
        ).pack(side="left", padx=4)

        dir_row = tk.Frame(sf, bg="white")
        dir_row.pack(fill="x", pady=5)
        tk.Label(
            dir_row, text="Save files to:", width=14,
            anchor="w", bg="white", font=("Helvetica", 11),
        ).pack(side="left")
        ttk.Entry(
            dir_row, textvariable=self.output_dir,
            width=42, font=("Helvetica", 11),
        ).pack(side="left", padx=4)
        ttk.Button(
            dir_row, text="Browse…",
            command=self._browse_dir,
        ).pack(side="left", padx=4)

        # What to download
        wf = ttk.LabelFrame(
            self.main, text=" 📥  What to download ", padding=14
        )
        wf.pack(fill="x", pady=(0, 10))

        for var, label, desc in [
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
             "Articles and book chapters on course reserve"),
        ]:
            row = tk.Frame(wf, bg="white")
            row.pack(fill="x", pady=4)
            ttk.Checkbutton(row, text=label, variable=var).pack(side="left")
            tk.Label(
                row, text=f"  —  {desc}",
                fg="#666", bg="white", font=("Helvetica", 9),
            ).pack(side="left")

        # Options
        of = ttk.LabelFrame(self.main, text=" 🔧  Options ", padding=14)
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
            text="Skip video files  (saves disk space)",
            variable=self.skip_videos,
        ).pack(side="left")

        # Login banner (hidden until needed)
        self.login_frame = tk.Frame(
            self.main, bg="#fff3cd", pady=14, padx=16,
            relief="solid", bd=2,
        )
        tk.Label(
            self.login_frame,
            text="🔐  Login required",
            font=("Helvetica", 13, "bold"),
            bg="#fff3cd", fg="#856404",
        ).pack(anchor="w")
        tk.Label(
            self.login_frame,
            text=(
                "A browser window has opened. "
                "Please log in with your university credentials.\n"
                "Once you can see your Canvas dashboard, "
                "click the button below to continue."
            ),
            font=("Helvetica", 11),
            bg="#fff3cd", fg="#533f03",
            justify="left",
        ).pack(anchor="w", pady=(4, 10))
        self._login_btn = tk.Button(
            self.login_frame,
            text="  ✅  I'm logged in — continue downloading  ",
            font=("Helvetica", 13, "bold"),
            bg="#28a745", fg="white",
            activebackground="#218838",
            activeforeground="white",
            relief="raised", bd=3,
            cursor="hand2",
            command=self._confirm_login,
        )
        self._login_btn.pack(anchor="w")

        # Log
        lf = ttk.LabelFrame(self.main, text=" 📋  Progress log ", padding=8)
        lf.pack(fill="both", expand=True, pady=(0, 6))

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
            ("login",   "#ffcc02"),
        ]:
            self.log_text.tag_config(tag, foreground=colour)

        # Status bar (sits between log and bottom controls)
        self.status_frame = tk.Frame(
            self.root, bg="#d0d0d0", pady=6, padx=24
        )
        self.status_frame.pack(fill="x")
        self.status_var = tk.StringVar(
            value="Ready — click 'Start Download' to begin."
        )
        self.status_label = tk.Label(
            self.status_frame,
            textvariable=self.status_var,
            fg="#222", bg="#d0d0d0",
            font=("Helvetica", 11, "bold"),
            anchor="w",
        )
        self.status_label.pack(fill="x")

        # Bottom controls
        ctrl = tk.Frame(self.root, bg="#e8e8e8", pady=12, padx=24)
        ctrl.pack(fill="x")

        self.start_btn = tk.Button(
            ctrl,
            text="▶   Start Download",
            font=("Helvetica", 13, "bold"),
            bg="#4a148c", fg="white",
            activebackground="#6a1fbc",
            activeforeground="white",
            relief="raised", bd=3,
            cursor="hand2",
            padx=16, pady=8,
            command=self._start,
        )
        self.start_btn.pack(side="left", padx=(0, 12))

        self.stop_btn = tk.Button(
            ctrl,
            text="⏹  Stop",
            font=("Helvetica", 12),
            bg="#cccccc", fg="#444444",
            relief="raised", bd=2,
            cursor="hand2",
            padx=12, pady=8,
            state="disabled",
            command=self._stop,
        )
        self.stop_btn.pack(side="left")

    # ── Login banner ──────────────────────────────────────────────────────────

    def _show_login_bar(self):
        if self._login_bar_visible:
            return
        self._login_bar_visible = True
        self._login_btn.config(state="normal", bg="#28a745")
        self.login_frame.pack(fill="x", pady=(0, 10))
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(300, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()
        self._set_status(
            "⏸  Waiting for login — click the green button above",
            "#856404", "#fff3cd",
        )

    def _hide_login_bar(self):
        if not self._login_bar_visible:
            return
        self._login_bar_visible = False
        self.login_frame.pack_forget()

    def _confirm_login(self):
        self._login_btn.config(state="disabled", bg="#6c757d")
        try:
            SENTINEL_FILE.write_text("ready", encoding="utf-8")
        except Exception as exc:
            self._log(f"  ⚠  Could not write sentinel: {exc}\n", "warn")
        self._hide_login_bar()
        self._log("  ✅  Login confirmed — continuing…\n\n", "success")
        self._set_status("Continuing download…", "#155724", "#d4edda")

    # ── Status bar helpers ────────────────────────────────────────────────────

    def _set_status(self, text: str,
                    fg: str = "#222", bg: str = "#d0d0d0"):
        self.status_var.set(text)
        self.status_label.config(fg=fg)
        self.status_frame.config(bg=bg)
        self.status_label.config(bg=bg)

    # ── Animated dots while running ───────────────────────────────────────────

    def _start_dots(self, base_text: str):
        self._dot_base = base_text
        self._dot_count = 0
        self._animate_dots()

    def _animate_dots(self):
        if not self.running or self._login_bar_visible:
            return
        self._dot_count = (self._dot_count + 1) % 4
        dots = "." * self._dot_count
        self._set_status(
            f"⏳  {self._dot_base}{dots}",
            "#0c5460", "#d1ecf1",
        )
        self._dot_job = self.root.after(600, self._animate_dots)

    def _stop_dots(self):
        if self._dot_job:
            self.root.after_cancel(self._dot_job)
            self._dot_job = None

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

        cfg = {
            "canvas_url":   canvas_url,
            "panopto_url":  self.panopto_url.get().strip().rstrip("/"),
            "output_dir":   self.output_dir.get().strip(),
            "skip_ongoing": self.skip_ongoing.get(),
            "skip_videos":  self.skip_videos.get(),
            "do_canvas":    self.do_canvas.get(),
            "do_external":  self.do_external.get(),
            "do_panopto":   self.do_panopto.get(),
            "do_reserves":  self.do_reserves.get(),
        }
        save_config(cfg)
        write_canvas_config(cfg["canvas_url"], cfg["panopto_url"])

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

        Path(out).mkdir(parents=True, exist_ok=True)

        if SENTINEL_FILE.exists():
            SENTINEL_FILE.unlink()

        self.running = True
        self._login_bar_visible = False
        self._hide_login_bar()
        self._clear_log()

        # Visual: disable Start, enable Stop with red colour
        self.start_btn.config(state="disabled", bg="#888888")
        self.stop_btn.config(
            state="normal", bg="#c0392b", fg="white",
            text="⏹  Stop",
        )

        self._log("═" * 58 + "\n", "header")
        self._log("  🎓  Canvas Archive — Starting\n", "header")
        self._log(f"  📁  Saving to: {out}\n", "info")
        self._log("═" * 58 + "\n\n", "header")

        self._run_next_script()

    def _stop(self):
        self.running = False
        self._stop_dots()

        if self.process:
            try:
                self.process.terminate()
            except Exception:
                pass
            self.process = None

        self._hide_login_bar()

        if SENTINEL_FILE.exists():
            try:
                SENTINEL_FILE.unlink()
            except Exception:
                pass

        # Visual: clearly show stopped state
        self.start_btn.config(
            state="normal", bg="#4a148c", fg="white",
            text="▶   Start Download",
        )
        self.stop_btn.config(
            state="disabled", bg="#cccccc", fg="#444444",
            text="⏹  Stop",
        )
        self._set_status(
            "⏹  Download stopped — click Start to begin again.",
            "#721c24", "#f8d7da",
        )
        self._log("\n" + "─" * 58 + "\n", "dim")
        self._log("  ⏹  Download stopped.\n", "warn")
        self._log("─" * 58 + "\n", "dim")

    def _run_next_script(self):
        if not self.running:
            return
        if not self.script_queue:
            self._all_done()
            return

        script_name, args = self.script_queue.pop(0)
        script_path = HERE / script_name

        if not script_path.exists():
            self._log(
                f"  ⚠  {script_name} not found — skipping.\n", "warn"
            )
            self.root.after(200, self._run_next_script)
            return

        friendly = {
            "canvas_downloader.py":   "Downloading course files",
            "external_downloader.py": "Downloading external readings",
            "panopto_downloader.py":  "Downloading lecture recordings",
            "reserves_downloader.py": "Downloading library reserves",
        }.get(script_name, f"Running {script_name}")

        self._stop_dots()
        self._start_dots(friendly)

        self._log(f"\n{'─' * 58}\n", "dim")
        self._log(f"  ▶  {friendly}…\n", "info")
        self._log(f"{'─' * 58}\n", "dim")

        # KEY FIX: PYTHONUNBUFFERED=1 forces Python to flush output
        # immediately so every line appears in the GUI in real time.
        env = os.environ.copy()
        env["CANVAS_ARCHIVE_GUI"]  = "1"
        env["PYTHONUNBUFFERED"]    = "1"   # ← THE FIX
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        try:
            self.process = subprocess.Popen(
                [sys.executable, "-u", str(script_path)] + args,
                # -u flag also forces unbuffered output
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                bufsize=1,
                universal_newlines=True,
                cwd=str(HERE),
                env=env,
            )
        except Exception as exc:
            self._log(
                f"  ✗  Could not start {script_name}: {exc}\n", "error"
            )
            self.root.after(500, self._run_next_script)
            return

        # Confirm in the log that the script actually started
        self._log(
            f"  ⚙  Started {script_name} "
            f"(PID {self.process.pid})\n", "dim"
        )

        def _reader():
            try:
                for line in self.process.stdout:
                    self.log_queue.put(("line", line))
            except Exception:
                pass
            self.process.wait()
            self.log_queue.put(("done", self.process.returncode))

        threading.Thread(target=_reader, daemon=True).start()

    def _all_done(self):
        self.running = False
        self.process = None
        self._stop_dots()
        self._hide_login_bar()

        if SENTINEL_FILE.exists():
            try:
                SENTINEL_FILE.unlink()
            except Exception:
                pass

        self.start_btn.config(
            state="normal", bg="#4a148c", fg="white",
            text="▶   Start Download",
        )
        self.stop_btn.config(
            state="disabled", bg="#cccccc", fg="#444444",
        )

        out = self.output_dir.get()
        self._set_status(
            "✅  All done!  Click Start to run again.",
            "#155724", "#d4edda",
        )
        self._log("\n" + "═" * 58 + "\n", "success")
        self._log("  ✅  All downloads complete!\n", "success")
        self._log(f"  📁  Files saved to:\n      {out}\n", "success")
        self._log("═" * 58 + "\n", "success")
        messagebox.showinfo(
            "Download Complete! 🎓",
            f"All done!\n\nYour files have been saved to:\n\n{out}",
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
        try:
            while True:
                kind, data = self.log_queue.get_nowait()

                if kind == "line":
                    line = data

                    if any(c in line for c in
                           ("✓", "FINISHED", "Downloaded",
                            "complete", "✅")):
                        tag = "success"
                    elif any(c in line for c in
                             ("✗", "FAILED", "Error", "error",
                              "Traceback", "Exception")):
                        tag = "error"
                    elif any(c in line for c in
                             ("⚠", "WARNING", "timed out",
                              "Waiting", "skipped")):
                        tag = "warn"
                    elif any(c in line for c in
                             ("═", "─", "📚", "📹", "📖",
                              "🎓", "🌐", "▶", "⚙")):
                        tag = "header"
                    elif any(p in line for p in _LOGIN_PHRASES):
                        tag = "login"
                    else:
                        tag = None

                    self._log(line, tag)

                    # Show login banner
                    if any(p in line for p in _LOGIN_PHRASES):
                        if not self._login_bar_visible:
                            self._stop_dots()
                            self.root.after(300, self._show_login_bar)

                elif kind == "done":
                    rc = data
                    self._stop_dots()
                    self._hide_login_bar()
                    if rc == 0:
                        self._log("  ✓  Step complete.\n", "success")
                    else:
                        self._log(
                            f"  ⚠  Finished with exit code {rc}.\n",
                            "warn",
                        )
                    self.root.after(600, self._run_next_script)

        except queue.Empty:
            pass

        self.root.after(100, self._poll_log)


# ─────────────────────────────  STARTUP CHECKS  ───────────────────────────────

def check_setup() -> bool:
    missing = [s for s in REQUIRED_SCRIPTS if not (HERE / s).exists()]
    if missing:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Missing files",
            "Some required files are missing:\n\n"
            + "\n".join(f"  • {s}" for s in missing)
            + "\n\nMake sure all files are in the same folder.",
        )
        root.destroy()
        return False
    return True


def check_packages() -> bool:
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
            "Please run the setup script:\n"
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
    root.update_idletasks()
    w, h = 920, 800
    x = (root.winfo_screenwidth()  - w) // 2
    y = (root.winfo_screenheight() - h) // 2
    root.geometry(f"{w}x{h}+{x}+{y}")

    CanvasArchiveApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()