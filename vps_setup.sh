#!/bin/bash
# @your_account Nova Autoposter — VPS Setup Script
# Run this once on your Hostinger VPS after copying the project files over.
#
# Usage:
#   1. Copy project to VPS (run on your Mac):
#      scp -r "$HOME/ig-auto" user@YOUR_VPS_IP:/home/user/ig-auto
#      scp "$HOME/ig-auto/.env" user@YOUR_VPS_IP:/home/user/ig-auto/
#      scp "$HOME/ig-auto/credentials.json" user@YOUR_VPS_IP:/home/user/ig-auto/
#
#   2. SSH into VPS and run:
#      cd /home/user/ig-auto
#      chmod +x vps_setup.sh
#      ./vps_setup.sh

set -e

echo "============================================"
echo "  @your_account Nova Autoposter — VPS Setup"
echo "============================================"

# ── 1. System dependencies ────────────────────────────────────────────────────
echo ""
echo "[1/5] Installing system dependencies..."
apt-get update -qq
apt-get install -y python3 python3-pip python3-venv ffmpeg git curl > /dev/null
echo "  ✓ Python, ffmpeg, git installed"

# Confirm versions
PYTHON_VERSION=$(python3 --version)
FFMPEG_VERSION=$(ffmpeg -version 2>&1 | head -n1)
echo "  ✓ $PYTHON_VERSION"
echo "  ✓ $FFMPEG_VERSION"

# ── 2. Python virtual environment ────────────────────────────────────────────
echo ""
echo "[2/5] Setting up Python virtual environment..."
cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    echo "  ✓ Virtual environment created"
else
    echo "  ✓ Virtual environment already exists"
fi

# ── 3. Install Python packages ────────────────────────────────────────────────
echo ""
echo "[3/5] Installing Python packages..."
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt -q
echo "  ✓ All packages installed"

# ── 4. Check required secret files ───────────────────────────────────────────
echo ""
echo "[4/5] Checking required files..."

MISSING=0

if [ ! -f ".env" ]; then
    echo "  ✗ .env not found"
    MISSING=1
else
    echo "  ✓ .env found"
fi

if [ ! -f "credentials.json" ]; then
    echo "  ✗ credentials.json not found"
    MISSING=1
else
    echo "  ✓ credentials.json found"
fi

if [ ! -d "music" ]; then
    mkdir -p music
    echo "  ✓ music/ folder created (add MP3s here)"
else
    echo "  ✓ music/ folder exists"
fi

mkdir -p logs temp

if [ $MISSING -eq 1 ]; then
    echo ""
    echo "  Missing files above. Copy them from your Mac:"
    echo "  scp ~/ig-auto/.env user@$(hostname -I | awk '{print $1}'):$PROJECT_DIR/"
    echo "  scp ~/ig-auto/credentials.json user@$(hostname -I | awk '{print $1}'):$PROJECT_DIR/"
    echo ""
    echo "  Then re-run this script."
    exit 1
fi

# ── 5. Run preflight check ────────────────────────────────────────────────────
echo ""
echo "[5/5] Running preflight check..."
.venv/bin/python3 preflight.py

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "============================================"
echo "  Setup complete!"
echo "============================================"
echo ""
echo "  Next steps:"
echo ""
echo "  1. Set up cron jobs (automated posting):"
echo "     chmod +x setup_cron.sh && ./setup_cron.sh"
echo ""
echo "  2. Or run manually:"
echo "     .venv/bin/python3 agent.py --scan"
echo "     .venv/bin/python3 agent.py --brief"
echo "     .venv/bin/python3 compile.py --nova"
echo "     .venv/bin/python3 post.py"
echo ""
echo "  3. Watch logs once cron is running:"
echo "     tail -f logs/post.log"
echo ""
