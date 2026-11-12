#!/usr/bin/env python3
"""
refresh_token.py — Automatically refresh the Instagram long-lived access token.

Long-lived tokens last ~60 days and can be refreshed by passing the current
long-lived token back through the exchange endpoint (no manual steps needed).

Run manually:
    python3 refresh_token.py

Or let the weekly cron job handle it (Sundays at 1am UTC).

Writes results to token_log.txt. Sends email alert on failure.
"""

import os
import datetime
import smtplib
from email.message import EmailMessage
from pathlib import Path

import requests
from dotenv import load_dotenv

ENV_FILE = Path(__file__).parent / ".env"
TOKEN_LOG_FILE = Path(__file__).parent / "token_log.txt"
GRAPH_API_BASE = "https://graph.facebook.com/v21.0"


def load_env():
    load_dotenv(ENV_FILE)
    load_dotenv(Path.home() / ".env.master")


def update_env_token(new_token):
    """Replace INSTAGRAM_ACCESS_TOKEN in project .env and/or ~/.env.master if present."""
    master = Path.home() / ".env.master"
    targets = []
    if ENV_FILE.exists():
        targets.append(ENV_FILE)
    if master.exists():
        targets.append(master)
    if not targets:
        targets = [ENV_FILE]

    for path in targets:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True) if path.exists() else []
        updated = []
        found = False
        for line in lines:
            if line.startswith("INSTAGRAM_ACCESS_TOKEN="):
                updated.append(f"INSTAGRAM_ACCESS_TOKEN={new_token}\n")
                found = True
            else:
                updated.append(line)
        if not found:
            updated.append(f"INSTAGRAM_ACCESS_TOKEN={new_token}\n")
        path.write_text("".join(updated), encoding="utf-8")


def write_token_log(status, message):
    """Append a timestamped line to token_log.txt."""
    now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{now} | {status} | {message}\n"
    with open(TOKEN_LOG_FILE, "a") as f:
        f.write(line)
    print(f"  Logged: {line.strip()}")


def send_failure_email(error_message):
    """Send an email alert if the token refresh fails."""
    sender = os.getenv("EMAIL_SENDER")
    password = os.getenv("EMAIL_PASSWORD")
    receiver = os.getenv("EMAIL_RECEIVER")

    if not all([sender, password, receiver]):
        print("  Email alert skipped — EMAIL_SENDER/PASSWORD/RECEIVER not set.")
        return

    msg = EmailMessage()
    msg["Subject"] = "⚠ @your_account Instagram token refresh FAILED"
    msg["From"] = sender
    msg["To"] = receiver
    msg.set_content(
        f"The automatic Instagram token refresh failed.\n\n"
        f"Error: {error_message}\n\n"
        f"Action required: Log in to Meta Developer Console, generate a new "
        f"short-lived token, update INSTAGRAM_ACCESS_TOKEN in .env, and run:\n"
        f"  python3 refresh_token.py\n\n"
        f"Without a valid token, posting will fail."
    )

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(sender, password)
            smtp.send_message(msg)
        print(f"  Failure alert sent to {receiver}.")
    except Exception as e:
        print(f"  Could not send failure email: {e}")


def main():
    load_env()

    app_id = os.getenv("INSTAGRAM_APP_ID")
    app_secret = os.getenv("INSTAGRAM_APP_SECRET")
    current_token = os.getenv("INSTAGRAM_ACCESS_TOKEN")

    if not app_id or not app_secret or not current_token:
        error = (
            "INSTAGRAM_APP_ID, INSTAGRAM_APP_SECRET, and INSTAGRAM_ACCESS_TOKEN must be set "
            "in project .env and/or ~/.env.master"
        )
        print(f"ERROR: {error}")
        write_token_log("FAILED", error)
        send_failure_email(error)
        raise SystemExit(1)

    print("Refreshing Instagram long-lived access token...")
    print(f"  Current token: ...{current_token[-10:]}")

    response = requests.get(
        f"{GRAPH_API_BASE}/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": current_token,
        },
    )

    data = response.json()

    if "access_token" not in data:
        error = data.get("error", {}).get("message", str(data))
        print(f"\nERROR: Token refresh failed — {error}")
        write_token_log("FAILED", error)
        send_failure_email(error)
        raise SystemExit(1)

    new_token = data["access_token"]
    expires_in = data.get("expires_in")

    # Compute expiry date
    if expires_in:
        expiry_dt = datetime.datetime.utcnow() + datetime.timedelta(seconds=int(expires_in))
        days = round(int(expires_in) / 86400)
        expiry_str = expiry_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        expiry_display = f"{expiry_str} (~{days} days)"
    else:
        expiry_str = "unknown"
        expiry_display = "unknown"

    print(f"  New token:     ...{new_token[-10:]}")
    print(f"  Expires:       {expiry_display}")

    print("\nUpdating env file(s) with new token...")
    update_env_token(new_token)
    print("  Token line updated in configured .env file(s).")

    write_token_log("SUCCESS", f"Expires: {expiry_display}")

    print(f"\n--- Token refresh complete ---")
    print(f"New token saved. Expires: {expiry_display}")
    print(f"Log: {TOKEN_LOG_FILE}")


if __name__ == "__main__":
    main()
