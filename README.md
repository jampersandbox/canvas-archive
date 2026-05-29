# 🎓 Canvas Archive
### Save everything from Canvas before you lose access

If you're graduating — or your institution access is about to expire —
this tool automatically downloads all your course materials from Canvas:

- 📄 All uploaded files (PDFs, slides, videos, documents)
- 🔗 External linked readings (JSTOR, Google Drive, websites)
- 🎬 Panopto lecture recordings
- 📚 Library reserve readings

Everything is saved neatly in folders organized by semester and course.

---

## 👀 What it looks like

```
canvas_downloads/
  Fall 2022/
    History of Ancient Christianity/
      syllabus.html
      readings/       ← PDFs and articles
      slides/         ← PowerPoint files
      videos/         ← video files
      panopto/        ← lecture recordings
      library_reserves/    ← reserve readings
      external_readings/   ← JSTOR, Drive, etc.
  Spring 2023/
    ...
```

---

## 🖥️ The app

The app has a simple graphical interface — no Terminal required once
you've done the one-time setup:

1. Choose your school's Canvas URL
2. Choose where to save your files
3. Click **Start Download**
4. Log in when a browser window appears
5. Done!

---

## ⚡ Quick start (3 steps)

### Step 1 — Install Python (if you don't have it yet)

**Check first** — open Terminal (Mac) or Command Prompt (Windows) and type:
```
python3 --version
```
If you see something like `Python 3.11.0` — you already have it!
If not, follow the instructions below.

<details>
<summary><b>📥 Install Python on Mac</b></summary>

1. Go to **https://python.org/downloads**
2. Click the big **"Download Python"** button
3. Open the downloaded file and run the installer
4. When the installer finishes, a Finder window opens —
   double-click **"Install Certificates.command"** (important!)
5. Check it worked: open Terminal and type `python3 --version`

</details>

<details>
<summary><b>📥 Install Python on Windows</b></summary>

1. Go to **https://python.org/downloads**
2. Click the big **"Download Python"** button
3. Open the downloaded file
4. ⚠️ **On the first screen, tick "Add Python to PATH"** before clicking Install
5. Click **"Install Now"**
6. Check it worked: open Command Prompt and type `python --version`

</details>

---

### Step 2 — Download this package

Click the green **"Code"** button at the top of this page,
then click **"Download ZIP"**.

Unzip it anywhere on your computer — your Desktop is fine.
You'll get a folder called `canvas-archive-main`.

---

### Step 3 — Run the setup (once only)

This downloads a browser and installs everything needed.
You only need to do this once, ever.

<details>
<summary><b>🍎 Mac instructions</b></summary>

1. Open **Terminal**
   (press Cmd+Space, type "Terminal", press Enter)
2. Type `cd ` (with a space), then drag the `canvas-archive-main`
   folder from Finder onto the Terminal window. Press Enter.
3. Type this and press Enter:
   ```
   bash setup_mac.sh
   ```
4. Wait for it to finish (about 5 minutes — it's downloading a browser)
5. When you see "Setup complete!", you'll find a file called
   **"Launch Canvas Archive.command"** in the folder

From now on, just double-click **"Launch Canvas Archive.command"** to run the app.

**If Mac says it can't be opened because it's from an unidentified developer:**
Right-click the file → "Open" → "Open" again.

</details>

<details>
<summary><b>🪟 Windows instructions</b></summary>

1. Open the `canvas-archive-main` folder
2. Double-click **setup_windows.bat**
3. If Windows shows a blue warning screen, click **"More info"**
   then **"Run anyway"**
4. A black window will appear and show progress —
   wait for it to say "Setup complete!" (about 5 minutes)
5. You'll now find a file called **"Launch Canvas Archive.bat"**
   in the folder

From now on, just double-click **"Launch Canvas Archive.bat"** to run the app.

</details>

---

## 🚀 Using the app

Double-click your launcher file and this window will appear:

```
┌──────────────────────────────────────────────────────┐
│  🎓  Canvas Archive                                  │
│  Save all your course materials                      │
├──────────────────────────────────────────────────────┤
│                                                      │
│  Canvas URL:  [ https://canvas.harvard.edu      ▼ ] │
│  Save to:     [ ~/Documents/canvas_downloads    📁 ] │
│                                                      │
│  ☑ Course files                                     │
│  ☑ External readings                                │
│  ☑ Lecture recordings                               │
│  ☑ Library reserves                                 │
│                                                      │
│  ☑ Skip administrative courses                      │
│  ☐ Skip video files                                 │
│                                                      │
│  [ ▶  Start Download ]  [ ⏹ Stop ]                  │
│                                                      │
└──────────────────────────────────────────────────────┘
```

**What to do:**

1. **Select your school** from the Canvas URL dropdown.
   If your school isn't listed, type your Canvas URL directly.
   It usually looks like `https://canvas.YOURSCHOOL.edu`.

2. **Choose where to save** your files, or leave it as the default.

3. **Tick what you want** to download.

4. Click **"Start Download"**.

5. A browser window will open — **log in with your university credentials**
   as you normally would, then click **OK** in the app to continue.

6. The app may ask you to log in again once or twice
   (once for Canvas, once for library databases).
   Each time, just log in and click OK.

7. Leave it running. A large archive can take several hours.
   You can leave it overnight.

---

## 📋 What to expect

| Script | What it does |
|--------|-------------|
| Canvas downloader | All files, videos, syllabi from Canvas itself |
| External downloader | JSTOR, Google Drive, linked websites |
| Panopto downloader | Lecture recordings |
| Reserves downloader | Library reserve readings (Leganto/Alma) |

When everything finishes, you'll see a completion message
and your files will be in the folder you chose.

---

## 💾 Disk space

Videos can be large — a full four years of lecture recordings
can easily be 50–100 GB.

If you're running low on space, tick **"Skip video files"** in the app.
You can always come back and run it again with videos included
if you get more space.

---

## 🔁 Resuming

If the download is interrupted (computer sleeps, internet drops, etc.),
just run the app again. Everything already downloaded is automatically
skipped — it picks up right where it left off.

---

## 🏫 Supported schools

The app includes a dropdown with common Canvas schools.
If yours isn't listed, just type your Canvas URL directly —
it works with any school that uses Canvas.

**Common Canvas URL patterns:**
- `https://canvas.SCHOOL.edu`
- `https://SCHOOL.instructure.com`
- `https://lms.SCHOOL.edu`

---

## ❓ Troubleshooting

<details>
<summary><b>"Python not found" error</b></summary>

Go back to Step 1 and install Python.
Make sure to tick "Add Python to PATH" on Windows.

</details>

<details>
<summary><b>"Permission denied" on Mac</b></summary>

Open Terminal, navigate to the folder, and type:
```
chmod +x setup_mac.sh
```
Then try running the setup again.

</details>

<details>
<summary><b>The browser doesn't appear</b></summary>

Look in your Dock (Mac) or Taskbar (Windows) for a Chrome icon.
It may have opened behind other windows.

</details>

<details>
<summary><b>A course shows 0 files</b></summary>

Some instructors lock their course files after the term ends.
This is normal and not a bug.

</details>

<details>
<summary><b>"Module not found" error</b></summary>

The setup didn't complete properly. Close everything,
run the setup script again, and try once more.

</details>

<details>
<summary><b>The app is slow / taking a long time</b></summary>

This is normal! Depending on how many courses you have and how many
videos, a full download can take anywhere from 30 minutes to several hours.
Just leave it running.

</details>

<details>
<summary><b>Something else went wrong</b></summary>

Take a screenshot of the error message and share it with someone
technical who can help. The log window in the app shows exactly
what went wrong.

</details>

---

## 🔒 Privacy & security

- This tool runs entirely on your own computer
- Your login credentials are never stored — only the browser session
  cookie is saved locally (in the `browser_profile/` folder)
- No data is sent anywhere other than your own school's Canvas server
- You can delete the `browser_profile/` folder at any time to clear
  your saved session

---

## 📝 Notes for advanced users

- All scripts can also be run from the command line individually
- See the `--help` flag on each script for available options
- The `browser_profile/` folder stores your Canvas session —
  delete it to force a fresh login
- All settings are saved in `canvas_config.json`
- Logs are written to `*.log` files in the same folder as the scripts

---

## 🙏 Credits

Built with:
- [Playwright](https://playwright.dev/python/) — browser automation
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — video downloading
- [requests](https://requests.readthedocs.io/) — HTTP
- [tqdm](https://tqdm.github.io/) — progress bars

---

## 📄 License

MIT — free to use, share, and modify.

---

*Made with care for graduating students everywhere. Good luck! 🎓*
```
# canvas-archive
Save all your Canvas course materials before you lose access
