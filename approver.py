#!/usr/bin/env python3
"""
approver.py — One-click post approval server for @your_account autoposter.

Runs on the VPS as a lightweight HTTP server. Handles "Post Now" and "Skip"
links sent in email confirmations.

Usage:
    python3 approver.py          # port 5000 by default
    APPROVER_PORT=8080 python3 approver.py

setup_cron.sh starts this automatically on boot via @reboot cron.
"""

import datetime
import os
import secrets
import subprocess

from config import PENDING_POST_FILE, ROOT_DIR
from flask import Flask, request
from state import clear_pending_post, load_pending_post, save_pending_post_data

app = Flask(__name__)

PROJECT_DIR = ROOT_DIR
PENDING_FILE = PENDING_POST_FILE
PYTHON = PROJECT_DIR / ".venv/bin/python3"
POST_SCRIPT = PROJECT_DIR / "post.py"


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_pending():
    data = load_pending_post()
    return data if data else None


def verify_token(token):
    pending = load_pending()
    if not pending:
        return None, "No post is currently queued."
    if pending.get("status") == "publishing":
        return None, "This post is already being published."
    expected = pending.get("approve_token", "")
    if not token or token != expected:
        return None, "This link is invalid or has already been used."
    return pending, None


def mark_publish_started(pending):
    updated = dict(pending)
    updated["status"] = "publishing"
    updated["publish_started_at"] = datetime.datetime.utcnow().isoformat() + "Z"
    updated["approval_request_id"] = secrets.token_urlsafe(8)
    save_pending_post_data(updated)
    return updated


def restore_queue_state(pending, error_message):
    restored = dict(pending)
    restored["status"] = "queued"
    restored["last_publish_error"] = error_message
    restored.pop("approval_request_id", None)
    save_pending_post_data(restored)


def page(title, body, color="#22c55e"):
    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
  body {{font-family:-apple-system,BlinkMacSystemFont,sans-serif;max-width:540px;
         margin:60px auto;padding:20px;color:#111;background:#f9fafb}}
  .card {{background:white;border-radius:12px;padding:32px;box-shadow:0 1px 4px rgba(0,0,0,.08)}}
  h2 {{color:{color};margin-top:0}}
  pre {{background:#f5f5f5;padding:14px;border-radius:8px;white-space:pre-wrap;font-size:13px;overflow:auto}}
</style>
</head>
<body><div class="card">{body}</div></body>
</html>"""


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/approve")
def approve():
    token = request.args.get("token", "")
    pending, err = verify_token(token)
    if err:
        return page("Error", f"<h2>&#10060; {err}</h2>", color="#ef4444"), 403

    pending = mark_publish_started(pending)

    try:
        result = subprocess.run(
            [str(PYTHON), str(POST_SCRIPT)],
            capture_output=True, text=True,
            cwd=str(PROJECT_DIR), timeout=120,
        )
        if result.returncode == 0:
            return page("Posted!", "<h2>&#9989; Posted to Instagram!</h2>"
                        f"<pre>{result.stdout}</pre>")
        else:
            restore_queue_state(pending, result.stderr or result.stdout or "Unknown publish failure.")
            return page("Failed", "<h2>&#10060; Post failed</h2>"
                        f"<pre>{result.stderr or result.stdout}</pre>",
                        color="#ef4444"), 500
    except subprocess.TimeoutExpired:
        restore_queue_state(pending, "Publish timed out while waiting for post.py to finish.")
        return page("Timeout",
                    "<h2>&#8987; Timed out</h2><p>Check VPS logs.</p>",
                    color="#f59e0b"), 504


@app.route("/skip")
def skip():
    token = request.args.get("token", "")
    pending, err = verify_token(token)
    if err:
        return page("Error", f"<h2>&#10060; {err}</h2>", color="#ef4444"), 403

    clear_pending_post()
    return page("Skipped",
                "<h2>&#9197; Skipped</h2><p>Post cleared. Nothing was published.</p>",
                color="#6b7280")


@app.route("/")
def index():
    pending = load_pending()
    if not pending or not pending.get("caption"):
        return page("No pending post",
                    "<h2>&#128248; No post queued</h2><p>Run main.py or compile.py first.</p>",
                    color="#6b7280")
    caption = pending["caption"][:300] + ("…" if len(pending["caption"]) > 300 else "")
    return page("Pending post",
                f"<h2>&#128248; Post queued</h2>"
                f"<p><strong>File:</strong> {pending.get('file_name', '—')}</p>"
                f"<p><strong>Status:</strong> {pending.get('status', 'queued')}</p>"
                f"<p><strong>Caption preview:</strong></p>"
                f"<pre>{caption}</pre>"
                f"<p>Use the approve link from your email to post.</p>")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("APPROVER_PORT", 5000))
    print(f"  Approval server → http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port)
