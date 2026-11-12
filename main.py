import os
import sys
import json
import random
import datetime
import mimetypes
from pathlib import Path

import anthropic
import pillow_heif
from PIL import Image
from config import AGENT_MEMORY_FILE, BRAND_PROMPT_FILE, CREDENTIALS_FILE, PENDING_POST_FILE, TEMP_DIR
from google.oauth2 import service_account
from locations import get_location_context
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from state import load_pending_post, load_posted_log, save_pending_post_data

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
FOLDER_NAME = os.getenv("GOOGLE_DRIVE_FOLDER_NAME")

SUPPORTED_IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif"}
SUPPORTED_VIDEO_TYPES = {".mp4", ".mov"}
SUPPORTED_TYPES = SUPPORTED_IMAGE_TYPES | SUPPORTED_VIDEO_TYPES

LOW_VAULT_THRESHOLD = 10

SUBFOLDER_CONTENT_TYPE = {
    "vn food": "food",
    "coffee/shops": "lifestyle",
    "exploring": "lifestyle",
    "misc": "lifestyle",
    "street scenes": "lifestyle",
    "ba na": "lifestyle",
    "hoi an": "lifestyle",
    "hoian": "lifestyle",
    "da nang": "lifestyle",
    "son tra": "lifestyle",
    "marble mountains": "lifestyle",
}

# From Brand_Prompt.txt content strategy
WEEKLY_TARGETS = {"food": 3, "lifestyle": 2, "heritage": 1, "motivational": 1}


def load_brand_prompt():
    with open(BRAND_PROMPT_FILE, "r", encoding="utf-8") as f:
        return f.read().strip()


def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        str(CREDENTIALS_FILE), scopes=SCOPES
    )
    return build("drive", "v3", credentials=creds)


def find_folder_id(service, folder_name):
    query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    result = service.files().list(q=query, fields="files(id, name)").execute()
    folders = result.get("files", [])
    if not folders:
        raise ValueError(f"Folder '{folder_name}' not found in Google Drive.")
    return folders[0]["id"]


def find_subfolder_id(service, parent_id, subfolder_name):
    query = (
        f"'{parent_id}' in parents and trashed=false "
        f"and mimeType='application/vnd.google-apps.folder' "
        f"and name='{subfolder_name}'"
    )
    result = service.files().list(q=query, fields="files(id, name)").execute()
    folders = result.get("files", [])
    if not folders:
        raise ValueError(f"Subfolder '{subfolder_name}' not found inside the vault.")
    return folders[0]["id"]


def list_drive_files(service, folder_id, subfolder_name=None):
    query = f"'{folder_id}' in parents and trashed=false"
    result = service.files().list(
        q=query,
        fields="files(id, name, mimeType, thumbnailLink)",
        pageSize=1000,
    ).execute()
    items = result.get("files", [])
    files = []
    for item in items:
        if item["mimeType"] == "application/vnd.google-apps.folder":
            files.extend(list_drive_files(service, item["id"], subfolder_name=item["name"]))
        else:
            item["subfolder_name"] = subfolder_name
            files.append(item)
    return files


def load_agent_memory():
    if not AGENT_MEMORY_FILE.exists():
        return {}
    try:
        with open(AGENT_MEMORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_agent_memory(memory):
    with open(AGENT_MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(memory, f, indent=2)


def get_week_content_balance(posted_log):
    """Count posts by content_type in the last 7 days."""
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=7)
    balance = {k: 0 for k in WEEKLY_TARGETS}
    for entry in posted_log.values():
        if not isinstance(entry, dict):
            continue
        posted_at_str = entry.get("posted_at", "")
        content_type = entry.get("content_type")
        if not posted_at_str or not content_type:
            continue
        try:
            posted_at = datetime.datetime.fromisoformat(posted_at_str.rstrip("Z"))
        except ValueError:
            continue
        if posted_at >= cutoff and content_type in balance:
            balance[content_type] += 1
    return balance


def most_needed_content_type(balance):
    """Return the content_type most behind its weekly target (by largest deficit ratio)."""
    best = None
    best_deficit = -1
    for ct, target in WEEKLY_TARGETS.items():
        if target == 0:
            continue
        deficit = (target - balance.get(ct, 0)) / target
        if deficit > best_deficit:
            best_deficit = deficit
            best = ct
    return best


def pick_smart_file(files, posted_log, pending_file, types):
    """
    Pick the next file to post using content pillar balance + subfolder rotation.
    Falls back to any unposted file if preferred type has no candidates.
    """
    memory = load_agent_memory()
    subfolder_history = memory.get("performance", {}).get("subfolder_history", [])
    recent_subfolders = set(subfolder_history[-3:])

    # All eligible unposted candidates
    candidates = [
        f for f in files
        if Path(f["name"]).suffix.lower() in types
        and f["name"] not in posted_log
        and f["name"] != pending_file
    ]
    if not candidates:
        return None

    balance = get_week_content_balance(posted_log)
    target_type = most_needed_content_type(balance)
    print(f"  Week balance: {balance}  →  targeting: {target_type}")

    def content_type_of(f):
        sub = (f.get("subfolder_name") or "").lower().strip()
        return SUBFOLDER_CONTENT_TYPE.get(sub, "lifestyle")

    # Prefer files matching the needed content type
    preferred = [f for f in candidates if content_type_of(f) == target_type]
    pool = preferred if preferred else candidates

    # Within pool, prefer subfolders not posted from recently
    rotated = [f for f in pool if (f.get("subfolder_name") or "").lower() not in recent_subfolders]
    return rotated[0] if rotated else pool[0]


def update_subfolder_history(memory, subfolder_name):
    perf = memory.setdefault("performance", {})
    history = perf.get("subfolder_history", [])
    history.append((subfolder_name or "").lower())
    perf["subfolder_history"] = history[-10:]


def download_file(service, file_id, file_name):
    TEMP_DIR.mkdir(exist_ok=True)
    local_path = TEMP_DIR / file_name
    request = service.files().get_media(fileId=file_id)
    with open(local_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return local_path


def convert_heic_to_jpeg(local_path):
    pillow_heif.register_heif_opener()
    jpeg_path = local_path.with_suffix(".jpg")
    img = Image.open(local_path)
    img.convert("RGB").save(jpeg_path, "JPEG")
    print(f"  Converted HEIC/HEIF to JPEG: {jpeg_path}")
    return jpeg_path


def extract_first_frame(video_path):
    """Extract the first frame of a video as a JPEG using FFmpeg binary.
    Returns the frame path, or None if ffmpeg binary is not installed."""
    try:
        import ffmpeg
    except ImportError:
        raise RuntimeError("ffmpeg-python is not installed. Run: pip install ffmpeg-python")
    frame_path = video_path.with_suffix(".thumb.jpg")
    try:
        (
            ffmpeg
            .input(str(video_path), ss=0)
            .output(str(frame_path), vframes=1, format="image2", vcodec="mjpeg")
            .overwrite_output()
            .run(quiet=True)
        )
        print(f"  Extracted first frame: {frame_path}")
        return frame_path
    except FileNotFoundError:
        print("  ffmpeg binary not found — skipping frame extraction.")
        print("  Install with: brew install ffmpeg")
        return None


def load_nova_context():
    """Load Nova's research + strategy to enrich caption prompts."""
    if not AGENT_MEMORY_FILE.exists():
        return ""
    try:
        with open(AGENT_MEMORY_FILE, encoding="utf-8") as f:
            memory = json.load(f)
        strategy = memory.get("strategy", {})
        research = memory.get("research", {})
        niches = research.get("niches", {})

        lines = []
        if strategy.get("week_summary"):
            lines.append(f"This week's content focus: {strategy['week_summary']}")
        if strategy.get("next_post_suggestion"):
            lines.append(f"Suggested angle: {strategy['next_post_suggestion']}")

        hooks = []
        for niche in niches.values():
            hooks.extend(niche.get("top_hook_formulas", [])[:2])
        if hooks:
            lines.append(f"Top performing hook formulas right now: {'; '.join(hooks[:4])}")

        avoid = []
        for niche in niches.values():
            avoid.extend(niche.get("oversaturated", [])[:1])
        if avoid:
            lines.append(f"Avoid these oversaturated angles: {'; '.join(avoid[:3])}")

        observed = memory.get("observed_trends", {})
        if observed.get("hook_patterns"):
            lines.append(
                f"\nOBSERVED TRENDING STYLES (from captions you have collected):\n"
                f"Hook patterns you like: {'; '.join(observed['hook_patterns'][:3])}\n"
                f"Tone elements: {'; '.join(observed.get('tone_elements', [])[:3])}\n"
                f"Apply to this caption: {'; '.join(observed.get('apply_to_account', [])[:2])}"
            )

        return "\n".join(lines) if lines else ""
    except Exception:
        return ""


def get_caption_style_weights():
    """Return (short_weight, long_weight). Biased toward better performer once enough data."""
    try:
        memory = load_agent_memory()
        style_stats = memory.get("performance", {}).get("caption_style", {})
        short_likes = style_stats.get("short", {}).get("avg_likes", 0)
        long_likes = style_stats.get("long", {}).get("avg_likes", 0)
        short_posts = style_stats.get("short", {}).get("posts", 0)
        long_posts = style_stats.get("long", {}).get("posts", 0)

        # Need at least 3 posts per style before biasing
        if short_posts < 3 or long_posts < 3:
            return 1.0, 1.0

        if short_likes > long_likes * 1.1:
            return 0.7, 0.3
        if long_likes > short_likes * 1.1:
            return 0.3, 0.7
    except Exception:
        pass
    return 1.0, 1.0


def evaluate_caption(caption, client):
    """
    Score a caption on 5 criteria. Returns (avg_score float, critique str).
    avg_score < 3.8 triggers a regeneration with the critique injected.
    """
    prompt = f"""Rate this Instagram caption on each criterion. Score 1 (fail) to 5 (perfect).

1. Does NOT start with the word "I"? (5=yes, 1=starts with I)
2. Contains a specific detail — price, dish name, or sensory fact? (5=yes, 1=generic)
3. Ends with a genuine answerable question from the audience's own life? (5=yes, 1=no or weak)
4. Sounds like a real person, not a travel blogger or brand account? (5=yes, 1=sounds generic/corporate)
5. Four sentences or fewer? (5=yes, 1=too long)

Caption:
{caption}

Respond with ONLY valid JSON — no extra text:
{{"scores": [n, n, n, n, n], "critique": "one sentence on the biggest weakness", "avg": n.n}}"""

    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=128,
        messages=[{"role": "user", "content": prompt}],
    )
    try:
        import json as _json
        data = _json.loads(msg.content[0].text.strip())
        return float(data.get("avg", 0)), data.get("critique", "")
    except Exception:
        return 5.0, ""


def generate_caption(local_path, brand_prompt, file_name, folder_name=None):
    import base64
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    ext = Path(file_name).suffix.lower()
    nova_context = load_nova_context()
    location_context = get_location_context(folder_name)
    grounding_instruction = (
        "GROUNDING CHECK — before writing, look at the image and ask: "
        "What do I actually see? Write only about that. "
        "Do not invent personal history, geography, or life context not visible here. "
        "Stay relatable, not confessional.\n\n"
    )

    short_style = "Write it short and playful — 1 to 2 sentences only. Cute, casual, and scroll-stopping."
    long_style = "Write it with depth — 3 to 4 sentences. Evocative, personal, and meaningful."
    styles = [("short", short_style), ("long", long_style)]
    w_short, w_long = get_caption_style_weights()
    style_label, caption_style = random.choices(styles, weights=[w_short, w_long], k=1)[0]

    if ext in SUPPORTED_IMAGE_TYPES:
        if ext in {".heic", ".heif"}:
            local_path = convert_heic_to_jpeg(local_path)
        mime_type = mimetypes.guess_type(local_path.name)[0] or "image/jpeg"
        with open(local_path, "rb") as f:
            image_data = f.read()

        encoded = base64.standard_b64encode(image_data).decode("utf-8")

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime_type,
                                "data": encoded,
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                f"{brand_prompt}\n\n"
                                + (f"NOVA STRATEGY CONTEXT:\n{nova_context}\n\n" if nova_context else "")
                                + location_context
                                + "IMPORTANT: Only name a specific dish or food if you can clearly identify it from the image. If uncertain, describe what you see — color, texture, appearance — instead of guessing the name.\n\n"
                                + grounding_instruction
                                + f"Write an Instagram caption for this image. {caption_style}"
                            ),
                        },
                    ],
                }
            ],
        )
    else:
        frame_path = extract_first_frame(local_path)

        if frame_path and frame_path.exists():
            with open(frame_path, "rb") as f:
                frame_data = f.read()
            encoded = base64.standard_b64encode(frame_data).decode("utf-8")
            content = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": encoded,
                    },
                },
                {
                    "type": "text",
                    "text": (
                        f"{brand_prompt}\n\n"
                        + (f"NOVA STRATEGY CONTEXT:\n{nova_context}\n\n" if nova_context else "")
                        + location_context
                        + "IMPORTANT: Only name a specific dish or food if you can clearly identify it from the image. If uncertain, describe what you see — color, texture, appearance — instead of guessing the name.\n\n"
                        + grounding_instruction
                        + f"This is the first frame of a video Reel. "
                        f"Write an Instagram Reel caption for this video. "
                        f"{caption_style} "
                        f"Do not reference anyone's appearance — this is a faceless account."
                    ),
                },
            ]
        else:
            print("  Generating caption from filename only (no frame available).")
            content = (
                f"{brand_prompt}\n\n"
                + location_context
                + grounding_instruction
                + f"Write an Instagram Reel caption for a video. "
                f"Filename hint: {file_name}. "
                f"{caption_style} "
                f"Do not reference anyone's appearance — this is a faceless account."
            )

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            messages=[{"role": "user", "content": content}],
        )

    caption = message.content[0].text.strip()

    # Self-evaluation: score the caption; regenerate once if it falls short
    score, critique = evaluate_caption(caption, client)
    print(f"  Caption score: {score:.1f}/5.0", end="")
    if score < 3.8 and critique:
        print(f" — regenerating ({critique})")
        retry_note = (
            f"\n\nPREVIOUS ATTEMPT CRITIQUE: {critique}\n"
            "Fix this specific issue. Keep the same voice and location.\n\n"
        )
        if ext in SUPPORTED_IMAGE_TYPES:
            # Re-encode the image for the retry call
            img_path = local_path if ext not in {".heic", ".heif"} else local_path.with_suffix(".jpg")
            with open(img_path, "rb") as f:
                retry_encoded = base64.standard_b64encode(f.read()).decode("utf-8")
            retry_mime = mimetypes.guess_type(img_path.name)[0] or "image/jpeg"
            retry_content = [
                {"type": "image", "source": {"type": "base64", "media_type": retry_mime, "data": retry_encoded}},
                {"type": "text", "text": (
                    f"{brand_prompt}\n\n"
                    + (f"NOVA STRATEGY CONTEXT:\n{nova_context}\n\n" if nova_context else "")
                    + location_context
                    + retry_note
                    + f"Write a better Instagram caption for this image. {caption_style}"
                )},
            ]
            retry_msg = client.messages.create(
                model="claude-sonnet-4-20250514", max_tokens=1024,
                messages=[{"role": "user", "content": retry_content}],
            )
        else:
            retry_msg = client.messages.create(
                model="claude-sonnet-4-20250514", max_tokens=1024,
                messages=[{"role": "user", "content": (
                    f"{brand_prompt}\n\n" + location_context + retry_note
                    + f"Write a better Instagram Reel caption. Filename: {file_name}. {caption_style} "
                    "Do not reference anyone's appearance — this is a faceless account."
                )}],
            )
        caption = retry_msg.content[0].text.strip()
        score2, _ = evaluate_caption(caption, client)
        print(f"  Retry score: {score2:.1f}/5.0")
    else:
        print()

    return caption, style_label


def save_pending_post(file_name, caption, file_id, subfolder=None, content_type=None, caption_style=None):
    ext = Path(file_name).suffix.lower()
    media_type = "video" if ext in SUPPORTED_VIDEO_TYPES else "photo"
    pending = {
        "file_name": file_name,
        "file_id": file_id,
        "caption": caption,
        "media_type": media_type,
        "subfolder": subfolder,
        "content_type": content_type,
        "caption_style": caption_style,
        "status": "queued",
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
    }
    save_pending_post_data(pending)
    return pending


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder", type=str, default=None, help="Only pick from a specific subfolder (e.g. 'ba na')")
    parser.add_argument("--photos-only", action="store_true", help="Only pick photos, skip videos")
    args = parser.parse_args()

    print("Loading brand prompt...")
    brand_prompt = load_brand_prompt()

    print("Loading posted log...")
    posted_log = load_posted_log()
    print(f"  {len(posted_log)} previously posted file(s).")

    existing_pending = load_pending_post()
    if existing_pending and existing_pending.get("status") in ("queued", "publishing"):
        pfn = existing_pending.get("file_name")
        if pfn:
            print(
                f"  Pending post already queued ({pfn}) — not selecting another file "
                "(avoids overwriting pending_post.json). Run post.py to publish."
            )
            return

    print("Connecting to Google Drive...")
    service = get_drive_service()

    vault_id = find_folder_id(service, FOLDER_NAME)

    if args.folder:
        print(f"  Targeting subfolder: {args.folder}")
        folder_id = find_subfolder_id(service, vault_id, args.folder)
    else:
        folder_id = vault_id
    print(f"  Scanning: {args.folder or FOLDER_NAME}")

    print("Scanning for media files...")
    files = list_drive_files(service, folder_id)
    print(f"  Found {len(files)} total file(s) in folder.")

    types = SUPPORTED_IMAGE_TYPES if args.photos_only else SUPPORTED_TYPES
    pending_file = existing_pending.get("file_name") if existing_pending else None

    # Low vault alert
    unposted_count = sum(
        1 for f in files
        if Path(f["name"]).suffix.lower() in types
        and f["name"] not in posted_log
        and f["name"] != pending_file
    )
    if unposted_count < LOW_VAULT_THRESHOLD:
        print(f"  WARNING: Only {unposted_count} unposted file(s) remaining — sending alert.")
        try:
            from notify import send_alert
            send_alert(
                "@your_account vault running low",
                f"Only {unposted_count} unposted files left. Upload more content to Google Drive."
            )
        except Exception as e:
            print(f"  Alert failed: {e}")

    chosen = pick_smart_file(files, posted_log, pending_file=pending_file, types=types)
    if not chosen:
        label = "photos" if args.photos_only else "media files"
        print(f"No unposted {label} found. All caught up!")
        sys.exit(1)

    file_name = chosen["name"]
    file_id = chosen["id"]
    folder_name = chosen.get("subfolder_name") or args.folder
    content_type = SUBFOLDER_CONTENT_TYPE.get((folder_name or "").lower().strip(), "lifestyle")
    print(f"\nSelected file: {file_name} (subfolder: {folder_name or 'vault root'}, type: {content_type})")

    # Update subfolder rotation history
    memory = load_agent_memory()
    update_subfolder_history(memory, folder_name)
    save_agent_memory(memory)

    print("Downloading file...")
    local_path = download_file(service, file_id, file_name)
    print(f"  Saved to: {local_path}")

    print("Generating caption with Claude...")
    caption, style_label = generate_caption(local_path, brand_prompt, file_name, folder_name=folder_name)
    print(f"  Caption style: {style_label}")

    char_count = len(caption)
    print(f"  Caption length: {char_count} chars", end="")
    if char_count > 2000:
        print(f" — WARNING: over 2000 chars (Instagram limit). Run regenerate.py or post.py will truncate.")
    else:
        print()

    pending = save_pending_post(
        file_name, caption, file_id,
        subfolder=folder_name,
        content_type=content_type,
        caption_style=style_label,
    )

    print("\n--- Summary ---")
    print(f"File:          {file_name}")
    print(f"Subfolder:     {folder_name or 'vault root'}")
    print(f"Content type:  {content_type}")
    print(f"Caption style: {style_label}")
    print(f"Timestamp:     {pending['timestamp']}")
    print(f"Caption:\n{caption}")
    print(f"\nSaved to {PENDING_POST_FILE.name}. Ready to post!")


if __name__ == "__main__":
    main()
