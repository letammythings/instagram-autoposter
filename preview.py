#!/usr/bin/env python3
"""
preview.py — Show the pending post details before publishing.

Displays file name, type, size, duration (videos only), and AI-generated caption.
Does not post or regenerate — use post.py / retry.py / regenerate.py for those.

Usage:
    python3 preview.py
"""

import sys
from pathlib import Path

from config import PENDING_POST_FILE, TEMP_DIR
from state import load_pending_post

VIDEO_EXTENSIONS = {".mp4", ".mov"}
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif"}


def resolve_media_type(file_name, pending):
    """Get media_type from pending dict if stored, else infer from extension."""
    if pending.get("media_type"):
        return pending["media_type"]
    return "video" if Path(file_name).suffix.lower() in VIDEO_EXTENSIONS else "photo"


def get_video_duration(video_path):
    """Return duration in seconds, or None if ffmpeg is unavailable."""
    try:
        import ffmpeg
        probe = ffmpeg.probe(str(video_path))
        return float(probe["format"]["duration"])
    except Exception:
        return None


def format_duration(seconds):
    if seconds is None:
        return "unknown"
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m}:{s:02d}"


def main():
    if not Path(PENDING_POST_FILE).exists():
        print(f"Nothing queued — {PENDING_POST_FILE.name} not found. Run main.py first.")
        sys.exit(0)

    pending = load_pending_post()

    if not pending:
        print(f"Nothing queued — {PENDING_POST_FILE.name} is empty. Run main.py first.")
        sys.exit(0)

    file_name = pending["file_name"]
    caption = pending["caption"]
    timestamp = pending.get("timestamp", "unknown")
    media_type = resolve_media_type(file_name, pending)

    # Resolve local file path (prefer converted JPEG for HEIC sources)
    local_path = TEMP_DIR / file_name
    if media_type == "photo":
        jpeg_path = local_path.with_suffix(".jpg")
        if jpeg_path.exists():
            local_path = jpeg_path

    W = 55
    print()
    print("=" * W)
    print("  PREVIEW — ig-autoposter")
    print("=" * W)
    print()
    print(f"  File:       {file_name}")
    print(f"  Type:       {'VIDEO (Reel)' if media_type == 'video' else 'PHOTO'}")
    print(f"  Queued at:  {timestamp}")
    print(f"  Status:     {pending.get('status', 'queued')}")

    if local_path.exists():
        size_mb = local_path.stat().st_size / (1024 * 1024)
        if media_type == "video":
            duration = get_video_duration(local_path)
            print(f"  Duration:   {format_duration(duration)}")
            print(f"  Size:       {size_mb:.1f}MB")
        else:
            print(f"  Size:       {size_mb:.2f}MB")
    else:
        print(f"  Local file: NOT FOUND at {local_path}")
        print(f"              Re-run main.py to re-download.")

    char_count = len(caption)
    char_warning = " ⚠ OVER LIMIT — will be truncated" if char_count > 2000 else ""
    print()
    print(f"  Caption ({char_count}/2000 chars){char_warning}:")
    print(f"  {'─' * 45}")
    for line in caption.split("\n"):
        print(f"  {line}")
    print(f"  {'─' * 45}")
    print()
    print("  Next steps:")
    print("    python3 post.py         — post it")
    print("    python3 regenerate.py   — regenerate caption")
    print("    python3 reset.py        — clear and skip")
    print("=" * W)


if __name__ == "__main__":
    main()
