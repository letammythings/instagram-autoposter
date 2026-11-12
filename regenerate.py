#!/usr/bin/env python3
"""
regenerate.py — Regenerate the caption for the pending post using Claude.

PHOTO:  Loads the image from temp/ → sends directly to Claude → new caption.
VIDEO:  Extracts first frame via FFmpeg → sends frame to Claude → Reel caption.

Saves the new caption back to pending_post.json.

Usage:
    python3 regenerate.py
"""

import sys
from pathlib import Path

from config import PENDING_POST_FILE, TEMP_DIR
from state import load_pending_post, save_pending_post_data

VIDEO_EXTENSIONS = {".mp4", ".mov"}


def resolve_media_type(file_name, pending):
    if pending.get("media_type"):
        return pending["media_type"]
    return "video" if Path(file_name).suffix.lower() in VIDEO_EXTENSIONS else "photo"


def main():
    if not Path(PENDING_POST_FILE).exists():
        print(f"No {PENDING_POST_FILE.name} found. Run main.py first.")
        sys.exit(1)

    pending = load_pending_post()

    if not pending:
        print(f"{PENDING_POST_FILE.name} is empty. Nothing to regenerate. Run main.py first.")
        sys.exit(1)

    file_name = pending["file_name"]
    media_type = resolve_media_type(file_name, pending)

    # Resolve local file path — prefer converted JPEG for HEIC sources
    local_path = TEMP_DIR / file_name
    if media_type == "photo":
        jpeg_path = local_path.with_suffix(".jpg")
        if jpeg_path.exists():
            local_path = jpeg_path

    if not local_path.exists():
        print(f"ERROR: Local file not found at {local_path}")
        print("Re-run main.py to re-download the file, then try again.")
        sys.exit(1)

    label = "VIDEO (Reel)" if media_type == "video" else "PHOTO"
    print(f"Regenerating caption for {label}: {file_name}")

    # Import shared caption generation from main.py
    from main import load_brand_prompt, generate_caption

    brand_prompt = load_brand_prompt()
    print("Sending to Claude...")
    new_caption = generate_caption(local_path, brand_prompt, file_name)

    pending["caption"] = new_caption
    pending["status"] = "queued"
    save_pending_post_data(pending)

    print(f"\nNew caption:\n{'─' * 45}")
    print(new_caption)
    print("─" * 45)
    print(f"\nSaved to {PENDING_POST_FILE.name}.")
    print("Run python3 preview.py to review, or python3 post.py to post.")


if __name__ == "__main__":
    main()
