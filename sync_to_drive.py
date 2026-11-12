#!/usr/bin/env python3
"""
sync_to_drive.py — Upload local CapCut video exports to Google Drive vault.

═══════════════════════════════════════════════════════════
HOW TO GET CAPCUT EXPORTS FROM IPHONE INTO THIS FOLDER
═══════════════════════════════════════════════════════════

OPTION A — AirDrop (quick, manual):
  1. Export Reel in CapCut → Save to Camera Roll
  2. Open Photos → select the video → Share → AirDrop → your Mac
  3. Video lands in ~/Downloads (or wherever AirDrop saves)
  4. Move it into ./capcut-exports/
  5. Run: python3 sync_to_drive.py

OPTION B — Apple Shortcuts (semi-automatic):
  1. Open the Shortcuts app on iPhone
  2. Tap + → Add Action
  3. Add these actions in order:
       • "Get Latest Videos" (set count = 1, from Camera Roll)
       • "Save File" → choose Google Drive → your vault folder
  4. After every CapCut export, tap the Shortcut once.
  Note: Requires Google Drive iOS app to be installed and signed in.

OPTION C — Google Drive iOS auto-backup (set-and-forget):
  1. Install Google Drive on iPhone
  2. Tap ☰ → Settings → Photos → Back up & sync → ON
  3. All new camera roll videos auto-upload to Drive/Computers.
  Con: Backs up EVERYTHING, not just CapCut exports.
  Workaround: In CapCut, export to a separate Album; use Shortcuts
  to watch that album.

OPTION D — Mac automation via folder watcher (most automatic):
  Run this script with --watch to poll continuously:
      python3 sync_to_drive.py --watch
  Drop AirDrop'd files into ./capcut-exports/ and they upload automatically.

═══════════════════════════════════════════════════════════

Usage:
    python3 sync_to_drive.py                        # Upload new files once
    python3 sync_to_drive.py --watch                # Poll every 60s
    python3 sync_to_drive.py --folder ~/Downloads   # Custom local folder
"""

import os
import sys
import json
import time
import mimetypes
from pathlib import Path

import config  # noqa: F401 — loads env from config.py before os.getenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/drive"]
CREDENTIALS_FILE = "credentials.json"
DRIVE_FOLDER_NAME = os.getenv("GOOGLE_DRIVE_FOLDER_NAME")
DEFAULT_LOCAL_FOLDER = Path("capcut-exports")
SYNCED_LOG_FILE = "synced_to_drive.json"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v"}
POLL_INTERVAL_SECONDS = 60


def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        CREDENTIALS_FILE, scopes=SCOPES
    )
    return build("drive", "v3", credentials=creds)


def find_or_create_folder(service, folder_name):
    query = (
        f"name='{folder_name}' and "
        "mimeType='application/vnd.google-apps.folder' and trashed=false"
    )
    result = service.files().list(q=query, fields="files(id, name)").execute()
    folders = result.get("files", [])
    if folders:
        return folders[0]["id"]
    meta = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    folder = service.files().create(body=meta, fields="id").execute()
    print(f"  Created folder '{folder_name}' in Drive (id: {folder['id']})")
    return folder["id"]


def load_synced_log():
    if not Path(SYNCED_LOG_FILE).exists():
        return {}
    with open(SYNCED_LOG_FILE) as f:
        return json.load(f)


def save_synced_log(log):
    with open(SYNCED_LOG_FILE, "w") as f:
        json.dump(log, f, indent=2)


def upload_file(service, local_path, folder_id):
    file_name = local_path.name
    mime_type = mimetypes.guess_type(str(local_path))[0] or "video/mp4"
    media = MediaFileUpload(str(local_path), mimetype=mime_type, resumable=True)
    meta = {"name": file_name, "parents": [folder_id]}
    result = service.files().create(body=meta, media_body=media, fields="id").execute()
    return result["id"]


def sync_once(local_folder, service, folder_id, synced_log):
    video_files = sorted(
        f for f in local_folder.iterdir()
        if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS
    )
    if not video_files:
        return 0

    to_upload = [f for f in video_files if f.name not in synced_log]
    if not to_upload:
        return 0

    uploaded = 0
    for video_path in to_upload:
        size_mb = video_path.stat().st_size / (1024 * 1024)
        print(f"\n  Uploading {video_path.name} ({size_mb:.1f}MB)...")
        try:
            file_id = upload_file(service, video_path, folder_id)
            synced_log[video_path.name] = {
                "drive_id": file_id,
                "size_mb": round(size_mb, 1),
                "synced_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            save_synced_log(synced_log)
            print(f"  Done → Drive file ID: {file_id}")
            uploaded += 1
        except Exception as e:
            print(f"  FAILED: {e}")

    return uploaded


def main():
    watch_mode = "--watch" in sys.argv

    local_folder = DEFAULT_LOCAL_FOLDER
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--folder" and i < len(sys.argv) - 1:
            local_folder = Path(sys.argv[i + 1]).expanduser()

    if not local_folder.exists():
        local_folder.mkdir(parents=True, exist_ok=True)
        print(f"Created local watch folder: {local_folder.resolve()}")
        print(f"Drop your CapCut exports here, then re-run this script.")
        return

    print(f"Connecting to Google Drive...")
    service = get_drive_service()
    folder_id = find_or_create_folder(service, DRIVE_FOLDER_NAME)
    print(f"Target folder: '{DRIVE_FOLDER_NAME}' (id: {folder_id})")
    print(f"Local folder:  {local_folder.resolve()}")

    synced_log = load_synced_log()

    if watch_mode:
        print(f"\nWatch mode active — polling every {POLL_INTERVAL_SECONDS}s. Ctrl+C to stop.\n")
        try:
            while True:
                count = sync_once(local_folder, service, folder_id, synced_log)
                if count:
                    print(f"\nSynced {count} new file(s) to Drive.")
                else:
                    print(f"  No new files. Waiting {POLL_INTERVAL_SECONDS}s...", end="\r")
                time.sleep(POLL_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            print("\nStopped.")
    else:
        count = sync_once(local_folder, service, folder_id, synced_log)
        if count:
            print(f"\nSync complete — {count} file(s) uploaded to '{DRIVE_FOLDER_NAME}'.")
            print(f"Log saved to {SYNCED_LOG_FILE}")
        else:
            total = len([
                f for f in local_folder.iterdir()
                if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS
            ])
            if total == 0:
                print(f"\nNo video files found in {local_folder.resolve()}")
                print(f"Supported: {', '.join(sorted(VIDEO_EXTENSIONS))}")
            else:
                print(f"\nAll {total} file(s) already synced to Drive. Nothing to do.")


if __name__ == "__main__":
    main()
