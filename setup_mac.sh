#!/bin/bash
# Canvas Archive — Mac Setup Script
# Run this once to install everything needed.

set -e

echo ""
echo "╔══════════════════════════════════════╗"
echo "║   Canvas Archive — Mac Setup         ║"
echo "╚══════════════════════════════════════╝"
echo ""

# ── Check Python ──────────────────────────────────────────────────────────────
if ! command -v python3 &> /dev/null; then
    echo "❌  Python 3 not found."
    echo "    Please install it from https://python.org/downloads"
    echo "    then run this script again."
    open "https://python.org/downloads"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(sys.version_info.minor)')
if [ "$PYTHON_VERSION" -lt 8 ]; then
    echo "❌  Python 3.8 or newer is required."
    echo "    Please update Python at https://python.org/downloads"
    open "https://python.org/downloads"
    exit 1
fi

echo "✅  Python $(python3 --version) found."
echo ""

# ── Virtual environment ───────────────────────────────────────────────────────
echo "📦  Setting up virtual environment..."
python3 -m venv venv
source venv/bin/activate

# ── Install packages ──────────────────────────────────────────────────────────
echo "📦  Installing required packages..."
pip install --quiet --upgrade pip
pip install --quiet requests tqdm playwright yt-dlp

echo "🌐  Downloading browser (this may take a few minutes)..."
playwright install chromium

# ── Patch scripts for multi-school support ────────────────────────────────────
echo "🔧  Configuring scripts..."
python patch_scripts.py

# ── Create launcher ───────────────────────────────────────────────────────────
LAUNCHER="Launch Canvas Archive.command"
cat > "$LAUNCHER" << 'LAUNCHEOF'
#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate
python canvas_archive.py
LAUNCHEOF
chmod +x "$LAUNCHER"

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║   ✅  Setup complete!                                ║"
echo "║                                                      ║"
echo "║   Double-click 'Launch Canvas Archive.command'      ║"
echo "║   to start the app.                                  ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""