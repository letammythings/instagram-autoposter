import os
import time
import datetime
from pathlib import Path

import anthropic
import requests
import cloudinary
import cloudinary.uploader
from config import PENDING_POST_FILE, POSTED_LOG_FILE, TEMP_DIR
from state import clear_pending_post, load_pending_post, load_posted_log, save_pending_post_data, save_posted_log

GRAPH_API_BASE = "https://graph.facebook.com/v21.0"
VIDEO_EXTENSIONS = {".mp4", ".mov"}
MAX_VIDEO_SIZE_MB = 95
MAX_CAPTION_CHARS = 2000


def require_pending_post():
    data = load_pending_post()
    if not data:
        raise ValueError(f"{PENDING_POST_FILE.name} is empty — run main.py first.")
    return data


def is_video(file_name):
    return Path(file_name).suffix.lower() in VIDEO_EXTENSIONS


def compress_video_if_needed(video_path):
    """Compress video to 720p if over MAX_VIDEO_SIZE_MB. Returns (path, orig_mb, final_mb)."""
    try:
        import ffmpeg
    except ImportError:
        raise RuntimeError("ffmpeg-python not installed. Run: pip install ffmpeg-python")

    original_mb = video_path.stat().st_size / (1024 * 1024)
    print(f"  Original size: {original_mb:.1f}MB")

    if original_mb <= MAX_VIDEO_SIZE_MB:
        return video_path, original_mb, original_mb

    print(f"  Over {MAX_VIDEO_SIZE_MB}MB limit — compressing to 720p...")
    compressed_path = video_path.with_stem(video_path.stem + "_compressed")
    (
        ffmpeg
        .input(str(video_path))
        .output(
            str(compressed_path),
            vf="scale=-2:720",
            vcodec="libx264",
            crf=28,
            acodec="aac",
            audio_bitrate="128k",
        )
        .overwrite_output()
        .run(quiet=True)
    )
    final_mb = compressed_path.stat().st_size / (1024 * 1024)
    print(f"  Compressed: {original_mb:.1f}MB → {final_mb:.1f}MB")
    return compressed_path, original_mb, final_mb


def upload_to_cloudinary(media_path, file_name):
    cloudinary.config(
        cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
        api_key=os.getenv("CLOUDINARY_API_KEY"),
        api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    )

    if is_video(file_name):
        upload_path, orig_mb, final_mb = compress_video_if_needed(media_path)
        result = cloudinary.uploader.upload(
            str(upload_path),
            resource_type="video",
            chunk_size=6_000_000,  # 6MB chunks for large files
        )
        url = result["secure_url"]
        print(f"  Video uploaded to Cloudinary: {url}")
        if orig_mb != final_mb:
            print(f"  Size: {orig_mb:.1f}MB → {final_mb:.1f}MB (compressed)")
        return url
    else:
        result = cloudinary.uploader.upload(str(media_path), resource_type="image")
        url = result["secure_url"]
        print(f"  Image uploaded to Cloudinary: {url}")
        return url


def get_thumb_offset_ms(video_path):
    """Return a thumbnail offset at 25% of video duration (avoids black opening frames)."""
    try:
        import subprocess
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True, text=True, timeout=15,
        )
        duration_s = float(result.stdout.strip())
        offset_ms = int(duration_s * 0.25 * 1000)
        print(f"  Thumbnail offset: {offset_ms}ms (25% of {duration_s:.1f}s)")
        return offset_ms
    except Exception as e:
        print(f"  Could not determine video duration ({e}) — using default 2000ms offset.")
        return 2000


def create_media_container(user_id, access_token, media_url, caption, file_name, thumb_offset_ms=None):
    params = {
        "caption": caption,
        "access_token": access_token,
    }
    if is_video(file_name):
        params["media_type"] = "REELS"
        params["video_url"] = media_url
        params["share_to_feed"] = "true"
        if thumb_offset_ms is not None:
            params["thumb_offset"] = thumb_offset_ms
    else:
        params["image_url"] = media_url

    response = requests.post(
        f"{GRAPH_API_BASE}/{user_id}/media",
        params=params,
    )
    response.raise_for_status()
    container_id = response.json()["id"]
    print(f"  Media container created: {container_id}")
    return container_id


def poll_container_status(access_token, container_id, timeout=300, interval=5):
    """Poll until status_code == FINISHED. Required for Reels *and* public image posts from URL."""
    print("  Waiting for Instagram to finish processing the media container...")
    elapsed = 0
    while elapsed < timeout:
        r = requests.get(
            f"{GRAPH_API_BASE}/{container_id}",
            params={"fields": "status_code", "access_token": access_token},
        )
        r.raise_for_status()
        status = r.json().get("status_code")
        print(f"  Container status: {status} ({elapsed}s elapsed)")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram container processing failed: {r.json()}")
        time.sleep(interval)
        elapsed += interval
    raise TimeoutError(
        f"Media container did not reach FINISHED within {timeout}s (last polls may show IN_PROGRESS)"
    )


def publish_container(user_id, access_token, container_id):
    response = requests.post(
        f"{GRAPH_API_BASE}/{user_id}/media_publish",
        params={
            "creation_id": container_id,
            "access_token": access_token,
        },
    )
    if not response.ok:
        detail = {}
        try:
            detail = response.json()
        except Exception:
            detail = {"raw": response.text[:800]}
        err = detail.get("error", detail)
        raise RuntimeError(
            f"Instagram media_publish HTTP {response.status_code}: {err}"
        )
    post_id = response.json()["id"]
    print(f"  Published successfully. Post ID: {post_id}")
    return post_id


def update_posted_log(posted_log, file_name, media_type, post_id,
                      subfolder=None, content_type=None, caption_style=None):
    posted_log[file_name] = {
        "posted_at": datetime.datetime.utcnow().isoformat() + "Z",
        "media_type": media_type,
        "post_id": post_id,
        "subfolder": subfolder,
        "content_type": content_type,
        "caption_style": caption_style,
    }
    save_posted_log(posted_log)


def generate_story_text(feed_caption):
    """Generate a punchy Story-specific caption from the feed caption. Non-fatal if it fails."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=80,
            messages=[{"role": "user", "content": (
                "You write Instagram Stories text for @your_account — a faceless expat lifestyle creator "
                "living abroad. Dry humor, specific, never generic.\n\n"
                f"Feed caption: {feed_caption}\n\n"
                "Write ONE short Story overlay line (max 8 words). "
                "POV/reaction style. Casual and punchy. 1-2 emojis max. "
                "Examples: 'pov: $2 lunch destroyed me 🍜' | 'current office. no complaints ☕' | "
                "'my grandmother's recipe > everything'\n\n"
                "Reply with ONLY the story text, nothing else."
            )}],
        )
        return msg.content[0].text.strip()
    except Exception as e:
        print(f"  Story text generation failed (non-fatal): {e}")
        return None


def post_to_stories(user_id, access_token, image_url, story_text=None):
    """Post an image to Instagram Stories. Non-fatal if it fails."""
    params = {"image_url": image_url, "media_type": "STORIES", "access_token": access_token}
    if story_text:
        params["caption"] = story_text
    r = requests.post(
        f"{GRAPH_API_BASE}/{user_id}/media",
        params=params,
    )
    r.raise_for_status()
    container_id = r.json()["id"]
    poll_container_status(access_token, container_id, timeout=120)
    r2 = requests.post(
        f"{GRAPH_API_BASE}/{user_id}/media_publish",
        params={"creation_id": container_id, "access_token": access_token},
    )
    r2.raise_for_status()
    print(f"  Story posted: {r2.json()['id']}")


def main():
    access_token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
    user_id = os.getenv("INSTAGRAM_USER_ID")
    if not access_token or not user_id:
        raise ValueError("INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_USER_ID must be set in .env")

    print("Loading pending post...")
    pending = require_pending_post()
    pending_status = pending.get("status", "queued")
    if pending_status not in {"queued", "publishing"}:
        raise ValueError(
            f"Pending post status is '{pending_status}' — reset or re-queue before posting."
        )

    file_name = pending["file_name"]

    # Guard: if pending already has a post_id, publish succeeded but cleanup crashed — just finish cleanup
    if pending.get("post_id"):
        stale_post_id = pending["post_id"]
        print(f"  Pending has post_id={stale_post_id} — publish already succeeded. Finishing cleanup.")
        posted_log = load_posted_log()
        if file_name not in posted_log:
            media_type = "video" if is_video(file_name) else "photo"
            update_posted_log(
                posted_log, file_name, media_type, stale_post_id,
                subfolder=pending.get("subfolder"),
                content_type=pending.get("content_type"),
                caption_style=pending.get("caption_style"),
            )
        clear_pending_post()
        return

    # Guard against re-posting a file that already succeeded but failed to clear pending
    posted_log = load_posted_log()
    if file_name in posted_log:
        print(f"  {file_name} already in posted_log — was already posted. Clearing stale pending.")
        clear_pending_post()
        return

    if pending_status != "publishing":
        pending["status"] = "publishing"
        pending["publish_started_at"] = datetime.datetime.utcnow().isoformat() + "Z"
        save_pending_post_data(pending)

    try:
        file_name = pending["file_name"]
        caption = pending["caption"]

        # Enforce Instagram's 2200-char limit with a safe buffer
        char_count = len(caption)
        print(f"Caption length: {char_count} chars")
        if char_count > MAX_CAPTION_CHARS:
            print(f"  WARNING: Caption is {char_count} chars — truncating to {MAX_CAPTION_CHARS}...")
            caption = caption[:MAX_CAPTION_CHARS].rsplit("\n", 1)[0]
            print(f"  Truncated to {len(caption)} chars.")

        # Resolve local path — prefer converted JPEG for HEIC sources
        local_path = TEMP_DIR / file_name
        if not is_video(file_name):
            jpeg_path = local_path.with_suffix(".jpg")
            if jpeg_path.exists():
                local_path = jpeg_path

        if not local_path.exists():
            raise FileNotFoundError(
                f"Local file not found: {local_path}. Re-run main.py to re-download."
            )

        print(f"File: {file_name}")
        print(f"Caption preview: {caption[:80]}...")

        media_type = "video" if is_video(file_name) else "image"
        print(f"Uploading {media_type} to Cloudinary for public URL...")
        media_url = upload_to_cloudinary(local_path, file_name)

        thumb_offset_ms = get_thumb_offset_ms(local_path) if is_video(file_name) else None

        print("Step 1: Creating Instagram media container...")
        container_id = create_media_container(user_id, access_token, media_url, caption, file_name, thumb_offset_ms)

        print("Step 1.5: Polling until container is FINISHED (required before publish)...")
        poll_container_status(access_token, container_id)

        print("Step 2: Publishing to Instagram...")
        post_id = publish_container(user_id, access_token, container_id)

        # Save post_id to pending immediately as crash tombstone — survives any subsequent failure
        pending["post_id"] = post_id
        save_pending_post_data(pending)

        # Write posted_log BEFORE Stories so a Stories failure can't cause a duplicate
        print(f"Updating {POSTED_LOG_FILE.name}...")
        posted_log = load_posted_log()
        media_type = "video" if is_video(file_name) else "photo"
        update_posted_log(
            posted_log, file_name, media_type, post_id,
            subfolder=pending.get("subfolder"),
            content_type=pending.get("content_type"),
            caption_style=pending.get("caption_style"),
        )

        if not is_video(file_name):
            try:
                print("Generating Story text...")
                story_text = generate_story_text(caption)
                if story_text:
                    print(f"  Story overlay: {story_text}")
                print("Posting to Stories...")
                post_to_stories(user_id, access_token, media_url, story_text=story_text)
            except Exception as e:
                print(f"  Stories post failed (non-fatal): {e}")

        print(f"Clearing {PENDING_POST_FILE.name}...")
        clear_pending_post()

        print(f"\n--- Success ---")
        print(f"Posted:  {file_name}")
        print(f"Post ID: {post_id}")
        print(f"Logged in {POSTED_LOG_FILE.name}.")
    except Exception as exc:
        pending["status"] = "queued"
        pending["last_publish_error"] = str(exc)
        save_pending_post_data(pending)
        raise


if __name__ == "__main__":
    main()
