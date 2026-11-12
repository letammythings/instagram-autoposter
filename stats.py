#!/usr/bin/env python3
"""
stats.py — Content statistics for the Google Drive vault.

Reads the Google Drive vault and posted_log.json to show:
  - Total photos and videos in vault
  - Total posted (photo vs video)
  - Total remaining (photo vs video)
  - Estimated days of content left at 1 post/day
  - Last 5 posts with date, filename, and type

Usage:
    python3 stats.py
"""

import os
from pathlib import Path
from datetime import datetime

from config import CREDENTIALS_FILE, POSTED_LOG_FILE
from google.oauth2 import service_account
from googleapiclient.discovery import build
from state import load_posted_log

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
FOLDER_NAME = os.getenv("GOOGLE_DRIVE_FOLDER_NAME")

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif"}
VIDEO_EXTS = {".mp4", ".mov"}


def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        str(CREDENTIALS_FILE), scopes=SCOPES
    )
    return build("drive", "v3", credentials=creds)


def find_folder_id(service, folder_name):
    query = (
        f"name='{folder_name}' and "
        "mimeType='application/vnd.google-apps.folder' and trashed=false"
    )
    result = service.files().list(q=query, fields="files(id, name)").execute()
    folders = result.get("files", [])
    if not folders:
        raise ValueError(f"Folder '{folder_name}' not found in Google Drive.")
    return folders[0]["id"]


def list_drive_files(service, folder_id):
    query = f"'{folder_id}' in parents and trashed=false"
    result = service.files().list(
        q=query,
        fields="files(id, name, mimeType)",
        pageSize=1000,
    ).execute()
    return result.get("files", [])


def classify_by_extension(file_name):
    """Return 'photo', 'video', or None based on file extension."""
    ext = Path(file_name).suffix.lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in PHOTO_EXTS:
        return "photo"
    return None


def log_entry_media_type(file_name, val):
    """
    Get media type from a posted_log entry.
    Handles both old format (string timestamp) and new format (dict with media_type).
    Falls back to extension classification for old entries.
    """
    if isinstance(val, dict) and "media_type" in val:
        return val["media_type"]
    return classify_by_extension(file_name)


def log_entry_posted_at(val):
    """Get the ISO timestamp string from a log entry (either format)."""
    if isinstance(val, str):
        return val
    return val.get("posted_at", "")


def format_posted_at(iso_str):
    try:
        dt = datetime.fromisoformat(iso_str.rstrip("Z"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso_str[:16] if iso_str else "unknown"


def main():
    print("Connecting to Google Drive...")
    service = get_drive_service()
    folder_id = find_folder_id(service, FOLDER_NAME)
    drive_files = list_drive_files(service, folder_id)
    print(f"  Found {len(drive_files)} file(s) in '{FOLDER_NAME}'.")

    # Vault breakdown
    vault_photos = [f for f in drive_files if classify_by_extension(f["name"]) == "photo"]
    vault_videos = [f for f in drive_files if classify_by_extension(f["name"]) == "video"]
    vault_names = {f["name"] for f in drive_files}

    # Posted log
    posted_log = load_posted_log()

    posted_photos = [
        (n, v) for n, v in posted_log.items()
        if log_entry_media_type(n, v) == "photo"
    ]
    posted_videos = [
        (n, v) for n, v in posted_log.items()
        if log_entry_media_type(n, v) == "video"
    ]

    # Remaining = in vault AND not yet posted
    remaining_photos = [f for f in vault_photos if f["name"] not in posted_log]
    remaining_videos = [f for f in vault_videos if f["name"] not in posted_log]
    total_remaining = len(remaining_photos) + len(remaining_videos)

    # Last 5 posts, newest first
    last_5 = sorted(
        posted_log.items(),
        key=lambda kv: log_entry_posted_at(kv[1]),
        reverse=True,
    )[:5]

    # ── Print report ────────────────────────────────────────
    W = 52
    print()
    print("=" * W)
    print("  STATS — @your_account")
    print("=" * W)

    print()
    print(f"  VAULT  (Google Drive / {FOLDER_NAME})")
    print(f"  {'Photos (HEIC/JPG/PNG):':<30} {len(vault_photos)}")
    print(f"  {'Videos (MP4/MOV):':<30} {len(vault_videos)}")
    print(f"  {'Total in vault:':<30} {len(vault_photos) + len(vault_videos)}")

    print()
    print("  POSTED")
    print(f"  {'Photos posted:':<30} {len(posted_photos)}")
    print(f"  {'Videos posted:':<30} {len(posted_videos)}")
    print(f"  {'Total posted:':<30} {len(posted_photos) + len(posted_videos)}")

    print()
    print("  REMAINING")
    print(f"  {'Photos remaining:':<30} {len(remaining_photos)}")
    print(f"  {'Videos remaining:':<30} {len(remaining_videos)}")
    print(f"  {'Total remaining:':<30} {total_remaining}")
    days_label = f"{total_remaining} day{'s' if total_remaining != 1 else ''}"
    print(f"  {'Est. days @ 1 post/day:':<30} {days_label}")

    print()
    print("  LAST 5 POSTS")
    if not last_5:
        print("  (no posts yet)")
    else:
        for file_name, val in last_5:
            posted_at = log_entry_posted_at(val)
            media_type = log_entry_media_type(file_name, val) or "unknown"
            label = "VIDEO" if media_type == "video" else "PHOTO"
            date_str = format_posted_at(posted_at)
            print(f"  [{label}]  {date_str}  {file_name}")

    print()
    print("=" * W)


if __name__ == "__main__":
    main()
