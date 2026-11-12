#!/bin/bash
# Nova Cron Job Setup Script
# Run this on your VPS when you're ready to automate posting.
#
# Usage:
#   chmod +x setup_cron.sh
#   ./setup_cron.sh
#
# What this sets up:
#   - Daily (Mon, Wed–Sat): 6:30am Vietnam — pick a photo from vault + post it
#     (cron: Tue–Fri at 23:30 UTC; Sunday handled separately by SUNDAY_NIGHT)
#   - Tuesday:              6pm Vietnam — pick a photo from vault + post it
#   - Weekly (Sun):         6am Vietnam — Nova brief + compile Reel + post it
#   - Weekly (Tue):         6am Vietnam — Nova research + scan + strategy
#
# Vietnam time = UTC+7
# So 6:30am Vietnam = 23:30 UTC previous day
# So 6pm Vietnam   = 11:00 UTC same day

set -e

# ── Config ────────────────────────────────────────────────────────────────────
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$PROJECT_DIR/.venv/bin/python3"
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"

echo "Setting up Nova cron jobs..."
echo "Project dir: $PROJECT_DIR"
echo "Python:      $PYTHON"

# ── Build cron entries ────────────────────────────────────────────────────────

# Approval server — start on reboot
APPROVER="@reboot cd $PROJECT_DIR && $PYTHON approver.py >> $LOG_DIR/approver.log 2>&1 &"

# Daily photo — Mon, Wed–Sat at 6:30am Vietnam = 23:30 UTC previous calendar day (Tue–Fri UTC)
# NOTE: Sunday is handled separately by SUNDAY_NIGHT — do NOT add day 0 here or it double-posts
DAILY_PHOTO="30 23 * * 2,3,4,5 cd $PROJECT_DIR && $PYTHON main.py --photos-only >> $LOG_DIR/post.log 2>&1 && $PYTHON post.py >> $LOG_DIR/post.log 2>&1"

# Tuesday photo — 6pm Vietnam (Tuesday 11:00 UTC)
TUESDAY_PHOTO="0 11 * * 2 cd $PROJECT_DIR && $PYTHON main.py --photos-only >> $LOG_DIR/post.log 2>&1 && $PYTHON post.py >> $LOG_DIR/post.log 2>&1"

# Weekly video — Sunday 6am Vietnam (Saturday 23:00 UTC)
WEEKLY_VIDEO="0 23 * * 6 cd $PROJECT_DIR && $PYTHON agent.py --brief >> $LOG_DIR/nova_brief.log 2>&1 && $PYTHON compile.py --nova >> $LOG_DIR/compile.log 2>&1 && $PYTHON post.py >> $LOG_DIR/post.log 2>&1"

# Weekly research — Tuesday 6am Vietnam (Monday 23:00 UTC, runs in parallel with daily photo)
WEEKLY_RESEARCH="0 23 * * 1 cd $PROJECT_DIR && $PYTHON agent.py --research >> $LOG_DIR/nova_research.log 2>&1 && $PYTHON agent.py --scan >> $LOG_DIR/nova_scan.log 2>&1 && $PYTHON agent.py --strategy >> $LOG_DIR/nova_strategy.log 2>&1"

# Sunday night post — 11:40pm UTC every Sunday (10 min after DAILY_PHOTO)
SUNDAY_NIGHT="40 23 * * 0 cd $PROJECT_DIR && $PYTHON main.py --photos-only >> $LOG_DIR/post.log 2>&1 && $PYTHON post.py >> $LOG_DIR/post.log 2>&1"

# Raw video processor — daily 2am Vietnam (19:00 UTC) — processes new raw videos into vault clips
PROCESS_RAW="0 19 * * * cd $PROJECT_DIR && $PYTHON process_raw.py >> $LOG_DIR/process_raw.log 2>&1"

# Engagement tracker — daily 2am UTC (9am Vietnam) — polls Graph API for likes/comments ~24h after posting
ENGAGEMENT_TRACKER="0 2 * * * cd $PROJECT_DIR && $PYTHON engagement_tracker.py >> $LOG_DIR/engagement.log 2>&1"

# ── Install cron jobs ─────────────────────────────────────────────────────────

# Read existing crontab (ignore error if empty)
EXISTING=$(crontab -l 2>/dev/null || true)

# Remove any old account entries (script lines AND their comment headers)
CLEANED=$(echo "$EXISTING" | grep -v "account\|agent.py\|compile.py\|post.py\|main.py\|approver.py\|process_raw.py\|engagement_tracker\|@your_account\|# ── @your_account\|# Approval server\|# Daily photo\|# Tuesday photo\|# Weekly video\|# Weekly research\|# Sunday night\|# Raw video processor\|# Engagement tracker" || true)

# Write new crontab
(
  echo "$CLEANED"
  echo ""
  echo "# ── @your_account Nova autoposter ──────────────────────────────"
  echo "# Approval server — starts on every reboot"
  echo "$APPROVER"
  echo "# Daily photo — Mon, Wed–Sat 6:30am Vietnam (Tue–Fri 23:30 UTC; Sunday = SUNDAY_NIGHT)"
  echo "$DAILY_PHOTO"
  echo "# Tuesday photo — 6pm Vietnam (Tue 11:00 UTC)"
  echo "$TUESDAY_PHOTO"
  echo "# Weekly video — Sunday 6am Vietnam (Saturday 23:00 UTC)"
  echo "$WEEKLY_VIDEO"
  echo "# Weekly research — Tuesday 6am Vietnam (Monday 23:00 UTC)"
  echo "$WEEKLY_RESEARCH"
  echo "# Sunday night post — 11:40pm UTC"
  echo "$SUNDAY_NIGHT"
  echo "# Raw video processor — daily 2am Vietnam (19:00 UTC)"
  echo "$PROCESS_RAW"
  echo "# Engagement tracker — daily 2am UTC (9am Vietnam)"
  echo "$ENGAGEMENT_TRACKER"
) | crontab -

echo ""
echo "Cron jobs installed. Current crontab:"
echo "─────────────────────────────────────────"
crontab -l
echo "─────────────────────────────────────────"
echo ""
echo "To disable all jobs:  crontab -r"
echo "To edit manually:     crontab -e"
echo "To view logs:         tail -f $LOG_DIR/post.log"
