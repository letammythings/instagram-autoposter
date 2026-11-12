#!/usr/bin/env python3
"""
reset.py — Clear pending_post.json back to empty {}.

Use this to abandon the current queued post without posting it.
The file will still remain in Google Drive and can be picked up again by main.py.

Usage:
    python3 reset.py
"""

from config import PENDING_POST_FILE
from state import clear_pending_post, load_pending_post


def main():
    if not PENDING_POST_FILE.exists():
        print(f"{PENDING_POST_FILE.name} does not exist. Nothing to reset.")
        return

    current = load_pending_post()

    if not current:
        print(f"{PENDING_POST_FILE.name} is already empty. Nothing to do.")
        return

    file_name = current.get("file_name", "(unknown)")
    media_type = current.get("media_type", "unknown")

    clear_pending_post()

    label = "VIDEO" if media_type == "video" else "PHOTO"
    print(f"Cleared {PENDING_POST_FILE.name}.")
    print(f"Abandoned: {file_name} [{label}]")
    print(f"Run main.py to queue the next file.")


if __name__ == "__main__":
    main()
