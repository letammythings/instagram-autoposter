#!/bin/bash
# sync_watch.sh — Auto-sync IG auto project to VPS on every file change.
# Runs as a background launchd service (starts on login).

PROJECT_DIR="$HOME/ig-auto"
VPS="user@YOUR_VPS_IP"
VPS_DIR="/home/user/ig-auto"
LOG="$PROJECT_DIR/logs/sync.log"

mkdir -p "$PROJECT_DIR/logs"

echo "[$(date)] Sync watcher started. Watching: $PROJECT_DIR" >> "$LOG"

fswatch -o \
  --exclude=".venv" \
  --exclude="__pycache__" \
  --exclude="\.pyc$" \
  --exclude="logs/" \
  --exclude="temp/" \
  --exclude="\.DS_Store" \
  --exclude="\.git/" \
  "$PROJECT_DIR" | while read -r _; do
    rsync -az --delete \
      --exclude='.venv' \
      --exclude='__pycache__' \
      --exclude='*.pyc' \
      --exclude='logs/' \
      --exclude='temp/' \
      --exclude='.DS_Store' \
      --exclude='.git/' \
      "$PROJECT_DIR/" "$VPS:$VPS_DIR/" \
      && echo "[$(date)] Synced OK" >> "$LOG" \
      || echo "[$(date)] Sync FAILED" >> "$LOG"
done
