#!/usr/bin/env python3
"""
compile.py — AI-powered Reel compilation using moviepy.

Flow:
  1. Scan Google Drive vault for unposted videos
  2. Download up to 10 candidate clips to temp/
  3. Extract 4 frames per clip via ffmpeg
  4. Claude Vision scores clips and selects 5-7
  5. Build moviepy composition with crossfades + music
  6. Add hook text overlay via ffmpeg drawtext
  7. Generate caption, save pending_post.json, send email

Usage:
    python3 compile.py
    python3 compile.py --nova
    python3 compile.py --folder "ba na"
"""

import base64
import datetime
import json
import os
import random
import subprocess
from pathlib import Path

import anthropic
import requests
from config import (
    AGENT_MEMORY_FILE,
    BRAND_PROMPT_FILE,
    CREDENTIALS_FILE,
    MUSIC_DIR,
    OUTPUT_FILE,
    PENDING_POST_FILE,
    TEMP_DIR,
    THUMBS_DIR,
)
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from locations import get_location_context
from state import load_posted_log, save_pending_post_data

SCOPES           = ["https://www.googleapis.com/auth/drive.readonly"]
FOLDER_NAME      = os.getenv("GOOGLE_DRIVE_FOLDER_NAME")
VIDEO_EXTENSIONS = {".mp4", ".mov"}
MUSIC_EXTENSIONS = {".mp3", ".wav"}
MAX_CANDIDATES   = 10
FRAMES_PER_CLIP  = 8
TARGET_W, TARGET_H = 1080, 1920
CROSSFADE_SEC    = 0.5


# ── Google Drive ───────────────────────────────────────────────────────────────

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


def list_drive_videos(service, folder_id):
    query = f"'{folder_id}' in parents and trashed=false"
    result = service.files().list(
        q=query,
        fields="files(id, name, mimeType)",
        pageSize=1000,
    ).execute()
    items = result.get("files", [])
    videos = []
    for item in items:
        if item["mimeType"] == "application/vnd.google-apps.folder":
            videos.extend(list_drive_videos(service, item["id"]))
        elif Path(item["name"]).suffix.lower() in VIDEO_EXTENSIONS:
            videos.append(item)
    return videos


def download_file(service, file_id, file_name):
    TEMP_DIR.mkdir(exist_ok=True)
    local_path = TEMP_DIR / file_name
    if local_path.exists():
        print(f"  Already cached: {file_name}")
        return local_path
    request = service.files().get_media(fileId=file_id)
    with open(local_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return local_path


# ── Frame extraction ───────────────────────────────────────────────────────────

def get_duration(video_path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
        capture_output=True, text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 10.0


def extract_frames(video_path, n=FRAMES_PER_CLIP):
    THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    duration = get_duration(video_path)
    frames = []
    for i in range(n):
        t = duration * (i + 1) / (n + 1)
        frame_path = THUMBS_DIR / f"{video_path.stem}_f{i}.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(t), "-i", str(video_path),
             "-vframes", "1", "-q:v", "3", str(frame_path)],
            capture_output=True,
        )
        if frame_path.exists():
            frames.append(frame_path)
    return frames


# ── Claude multi-frame selection ───────────────────────────────────────────────

def claude_score_and_select(clips_with_frames, trend_style=None):
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    content = []

    trend_context = ""
    if trend_style:
        avg_sec = trend_style.get("avg_clip_seconds", 3.0)
        energy  = trend_style.get("music_energy", "medium")
        trend_context = (
            f"\nTrending style: clips average {avg_sec:.1f}s, "
            f"music energy is {energy}. Prefer clips matching this energy.\n"
        )

    for clip in clips_with_frames:
        content.append({"type": "text", "text": f"\n--- Clip: {clip['name']} ---"})
        for frame_path in clip["frames"]:
            with open(frame_path, "rb") as f:
                img_data = base64.standard_b64encode(f.read()).decode("utf-8")
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": img_data},
            })

    content.append({
        "type": "text",
        "text": (
            f"Above are {FRAMES_PER_CLIP} frames each from {len(clips_with_frames)} clips "
            "for a Vietnamese / Da Nang lifestyle Instagram Reel (@your_account). Faceless account.\n"
            f"{trend_context}\n"
            "Score each clip on:\n"
            "- motion_energy (1-5): Is something moving? Dynamic action scores high.\n"
            "- visual_composition (1-5): Strong framing, color contrast, interesting angle.\n"
            "- emotional_resonance (1-5): Makes viewer feel hunger, wonder, calm, or joy.\n"
            "- story_role: label each as 'opening' (establishes place/mood), "
            "'build' (adds texture/detail), or 'payoff' (satisfying closer).\n\n"
            "Select exactly 5–7 clips. Order them as a story: "
            "1-2 opening shots → 2-3 build shots → 1 strong payoff. "
            "Variety matters — don't pick 3 food close-ups in a row.\n\n"
            "Reply with ONLY:\n"
            "CLIPS: filename1.mp4, filename2.mp4, ... (in story order)\n"
            "HOOK: 3-5 word hook phrase for text overlay (specific, not generic)"
        ),
    })

    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=512,
        messages=[{"role": "user", "content": content}],
    )
    raw = msg.content[0].text.strip()

    selected_names, hook_text = [], ""
    for line in raw.splitlines():
        if line.startswith("CLIPS:"):
            selected_names = [n.strip() for n in line.replace("CLIPS:", "").split(",") if n.strip()]
        elif line.startswith("HOOK:"):
            hook_text = line.replace("HOOK:", "").strip()

    print(f"  Claude selected {len(selected_names)} clips: {selected_names}")
    print(f"  Hook: {hook_text}")
    return selected_names, hook_text


# ── Trend style + Nova brief loaders ──────────────────────────────────────────

def load_trend_style():
    if not AGENT_MEMORY_FILE.exists():
        return None
    try:
        with open(AGENT_MEMORY_FILE) as f:
            return json.load(f).get("trend_style") or None
    except Exception:
        return None


def load_nova_brief():
    if not AGENT_MEMORY_FILE.exists():
        return None
    try:
        with open(AGENT_MEMORY_FILE) as f:
            brief = json.load(f).get("brief", {})
        return brief if brief.get("selected_clips") else None
    except Exception:
        return None


# ── Music ──────────────────────────────────────────────────────────────────────

def get_sonilo_music(video_path, mood="ambient"):
    """
    Request AI-generated music from Sonilo that matches the video's pacing and mood.
    Returns a local Path to the downloaded audio, or None on failure.
    Requires SONILO_API_KEY in .env. Free tier available at sonilo.com.
    """
    api_key = os.getenv("SONILO_API_KEY")
    if not api_key:
        return None

    audio_path = TEMP_DIR / "sonilo_music.mp3"
    try:
        print(f"  Requesting Sonilo music (mood={mood})...")
        with open(video_path, "rb") as vf:
            resp = requests.post(
                "https://sonilo.com/api/v1/generate",
                headers={"Authorization": f"Bearer {api_key}"},
                files={"video": (video_path.name, vf, "video/mp4")},
                data={"mood": mood, "format": "mp3"},
                timeout=120,
            )
        if not resp.ok:
            print(f"  Sonilo returned {resp.status_code} — falling back to local music.")
            return None

        data = resp.json()
        audio_url = data.get("audio_url") or data.get("url")
        if not audio_url:
            print("  Sonilo response missing audio_url — falling back.")
            return None

        audio_resp = requests.get(audio_url, timeout=60)
        audio_resp.raise_for_status()
        audio_path.write_bytes(audio_resp.content)
        print(f"  Sonilo music downloaded: {audio_path.name}")
        return audio_path
    except Exception as e:
        print(f"  Sonilo failed ({e}) — falling back to local music.")
        return None


def pick_music():
    if not MUSIC_DIR.exists():
        return None
    tracks = [f for f in MUSIC_DIR.iterdir() if f.suffix.lower() in MUSIC_EXTENSIONS]
    if not tracks:
        return None
    chosen = random.choice(tracks)
    print(f"  Music: {chosen.name}")
    return chosen


# ── Brand prompt + caption ─────────────────────────────────────────────────────

def load_brand_prompt():
    with open(BRAND_PROMPT_FILE, encoding="utf-8") as f:
        return f.read().strip()


def generate_reel_caption(video_path, brand_prompt, hook_text="", folder_name=None, trend_style=None):
    frame_path = THUMBS_DIR / (video_path.stem + "_caption.jpg")
    subprocess.run(
        ["ffmpeg", "-y", "-ss", "0", "-i", str(video_path),
         "-vframes", "1", "-q:v", "3", str(frame_path)],
        capture_output=True,
    )

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    location_context = get_location_context(folder_name)

    trend_hint = ""
    if trend_style:
        formats      = trend_style.get("trending_formats", [])
        hook_formats = trend_style.get("hook_text_formats", [])
        if formats:
            trend_hint += f"Trending content formats: {', '.join(formats[:3])}. "
        if hook_formats:
            trend_hint += f"Top hook styles: {', '.join(hook_formats[:3])}. "
        if trend_hint:
            trend_hint = "\n" + trend_hint + "\n"

    hook_note = f"\nThe video's hook overlay text is: '{hook_text}'. Write a caption that pairs with this hook." if hook_text else ""

    if frame_path.exists():
        with open(frame_path, "rb") as f:
            img_data = base64.standard_b64encode(f.read()).decode("utf-8")
        user_content = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_data}},
            {"type": "text", "text": (
                f"{brand_prompt}\n\n{location_context}{trend_hint}"
                "This is the first frame of a compiled Instagram Reel. "
                f"Write an Instagram Reel caption. MAX 4 sentences, under 2000 characters. "
                f"Punchy, scroll-stopping, conversational. No hashtags in caption body.{hook_note}"
            )},
        ]
    else:
        user_content = (
            f"{brand_prompt}\n\n{location_context}{trend_hint}"
            f"Write a compiled Reel caption. Max 4 sentences, punchy.{hook_note}"
        )

    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        messages=[{"role": "user", "content": user_content}],
    )
    return msg.content[0].text.strip()


# ── moviepy compilation ────────────────────────────────────────────────────────

def compile_reel(clip_paths, music_path, trend_style=None, output_file=None):
    from moviepy.editor import VideoFileClip, AudioFileClip, concatenate_videoclips

    if output_file is None:
        output_file = OUTPUT_FILE

    target_duration = 3.0
    if trend_style and trend_style.get("avg_clip_seconds"):
        target_duration = float(trend_style["avg_clip_seconds"])
        print(f"  Target clip duration from trends: {target_duration}s")

    print(f"  Loading {len(clip_paths)} clips...")
    processed = []
    for path in clip_paths:
        clip = VideoFileClip(str(path))

        # Crop to 9:16 from center
        clip_ratio = clip.w / clip.h
        target_ratio = TARGET_W / TARGET_H
        if clip_ratio > target_ratio:
            new_w = int(clip.h * target_ratio)
            clip = clip.crop(x1=(clip.w - new_w) // 2, width=new_w)
        elif clip_ratio < target_ratio:
            new_h = int(clip.w / target_ratio)
            clip = clip.crop(y1=(clip.h - new_h) // 2, height=new_h)

        clip = clip.resize((TARGET_W, TARGET_H))

        if clip.duration > target_duration:
            clip = clip.subclip(0, target_duration)

        processed.append(clip)
        print(f"    ✓ {path.name} ({clip.duration:.1f}s)")

    # Crossfades
    for i, clip in enumerate(processed):
        if i == 0:
            processed[i] = clip.crossfadeout(CROSSFADE_SEC)
        elif i == len(processed) - 1:
            processed[i] = clip.crossfadein(CROSSFADE_SEC)
        else:
            processed[i] = clip.crossfadein(CROSSFADE_SEC).crossfadeout(CROSSFADE_SEC)

    print("  Concatenating with crossfades...")
    video = concatenate_videoclips(processed, method="compose", padding=-CROSSFADE_SEC)

    if music_path and music_path.exists():
        print(f"  Adding music: {music_path.name}")
        audio = AudioFileClip(str(music_path))
        if audio.duration > video.duration:
            audio = audio.subclip(0, video.duration)
        audio = audio.audio_fadeout(2.0)
        video = video.set_audio(audio)

    TEMP_DIR.mkdir(exist_ok=True)
    print(f"  Rendering to {output_file.name}...")
    video.write_videofile(
        str(output_file),
        fps=30,
        codec="libx264",
        audio_codec="aac",
        preset="fast",
        logger=None,
    )

    for clip in processed:
        clip.close()
    video.close()

    return output_file


def add_text_overlay(video_path, hook_text):
    if not hook_text:
        return video_path
    output = video_path.with_stem(video_path.stem + "_text")
    safe_text = hook_text.replace("'", "\\'").replace(":", "\\:")
    result = subprocess.run([
        "ffmpeg", "-y", "-i", str(video_path),
        "-vf", (
            f"drawtext=text='{safe_text}'"
            ":fontsize=54:fontcolor=white"
            ":x=(w-text_w)/2:y=h-text_h-200"
            ":shadowcolor=black:shadowx=2:shadowy=2"
            ":enable='between(t,1,5)'"
        ),
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
        "-c:a", "copy",
        str(output),
    ], capture_output=True)
    if output.exists():
        video_path.unlink()
        output.rename(video_path)
    return video_path


# ── Save pending post ─────────────────────────────────────────────────────────

def save_pending_post(caption, output_file):
    pending = {
        "file_name": output_file.name,
        "file_id": None,
        "caption": caption,
        "media_type": "video",
        "status": "queued",
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
    }
    save_pending_post_data(pending)
    return pending


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--nova",   action="store_true", help="Use Nova compile brief")
    parser.add_argument("--folder", type=str, default=None, help="Only use clips from a specific subfolder")
    args = parser.parse_args()

    # Guard: don't overwrite a pending photo post that hasn't been published yet
    from state import load_pending_post
    existing_pending = load_pending_post()
    if existing_pending and existing_pending.get("status") in ("queued", "publishing"):
        pfn = existing_pending.get("file_name", "unknown")
        print(f"  Pending post already queued ({pfn}) — aborting compile to avoid overwriting it.")
        return

    # Unique filename per reel so posted_log can track each one independently
    reel_ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    output_file = TEMP_DIR / f"reel_{reel_ts}.mp4"

    print("Loading brand prompt...")
    brand_prompt = load_brand_prompt()

    print("Loading posted log...")
    posted_log = load_posted_log()

    print("Loading trend style...")
    trend_style = load_trend_style()
    if trend_style:
        print(f"  avg clip: {trend_style.get('avg_clip_seconds')}s  energy: {trend_style.get('music_energy')}")
    else:
        print("  No trend style — run: python3 agent.py --trends")

    print("Connecting to Google Drive...")
    service  = get_drive_service()
    vault_id = find_folder_id(service, FOLDER_NAME)
    folder_id = find_subfolder_id(service, vault_id, args.folder) if args.folder else vault_id

    print("Scanning for unposted videos...")
    all_videos = list_drive_videos(service, folder_id)
    unposted   = [v for v in all_videos if v["name"] not in posted_log]
    print(f"  {len(unposted)} unposted video(s) found.")

    if len(unposted) < 2:
        print("Not enough unposted videos (need at least 2). Exiting.")
        return

    hook_text  = ""
    local_clips = []

    # ── Nova mode ─────────────────────────────────────────────────────────────
    if args.nova:
        brief = load_nova_brief()
        if not brief:
            print("No Nova brief found. Run python3 agent.py --brief first. Falling back.")
            args.nova = False
        else:
            print(f"\nNova brief: {brief.get('theme', '')}")
            selected_names = brief.get("selected_clips", [])
            name_to_file   = {v["name"]: v for v in unposted}
            to_download    = [(n, name_to_file[n]["id"]) for n in selected_names if n in name_to_file]
            if len(to_download) < 2:
                print("  Not enough Nova clips in vault. Falling back.")
                args.nova = False
            else:
                print(f"  Downloading {len(to_download)} clips from Nova brief...")
                for name, file_id in to_download:
                    path = download_file(service, file_id, name)
                    local_clips.append(path)
                    print(f"    ✓ {name}")
                hook_text = brief.get("caption_angle", "")[:50]

    # ── Standard mode: multi-frame Claude selection ────────────────────────────
    if not args.nova:
        candidates = random.sample(unposted, min(MAX_CANDIDATES, len(unposted)))
        print(f"\nDownloading {len(candidates)} candidate clips for frame analysis...")
        clips_with_frames = []
        for v in candidates:
            print(f"  Downloading {v['name']}...")
            try:
                path   = download_file(service, v["id"], v["name"])
                frames = extract_frames(path)
                if frames:
                    clips_with_frames.append({"name": v["name"], "path": path, "frames": frames})
                    print(f"    ✓ {len(frames)} frames")
            except Exception as e:
                print(f"    Error: {e}")

        if len(clips_with_frames) < 2:
            print("Not enough clips with frames. Exiting.")
            return

        print(f"\nAsking Claude to score {len(clips_with_frames)} clips...")
        selected_names, hook_text = claude_score_and_select(clips_with_frames, trend_style)
        name_to_path = {c["name"]: c["path"] for c in clips_with_frames}
        local_clips  = [name_to_path[n] for n in selected_names if n in name_to_path]

        if len(local_clips) < 2:
            print("Claude returned fewer than 2 valid clips. Exiting.")
            return

    # ── Music ──────────────────────────────────────────────────────────────────
    print("\nChecking for music...")
    music_mood = "ambient"
    if args.nova:
        brief = load_nova_brief()
        if brief:
            music_mood = brief.get("music_mood", "ambient")
    music_path = get_sonilo_music(local_clips[0], mood=music_mood) or pick_music()

    # ── Compile ────────────────────────────────────────────────────────────────
    print("\nCompiling Reel with moviepy...")
    output_path = compile_reel(local_clips, music_path, trend_style, output_file=output_file)

    if hook_text:
        print(f"\nAdding hook text: '{hook_text}'")
        add_text_overlay(output_path, hook_text)

    # ── Caption ────────────────────────────────────────────────────────────────
    print("\nGenerating caption...")
    caption = generate_reel_caption(
        local_clips[0], brand_prompt,
        hook_text=hook_text, folder_name=args.folder, trend_style=trend_style,
    )
    print(f"  {len(caption)} chars: {caption[:80]}...")

    # ── Save ───────────────────────────────────────────────────────────────────
    print(f"\nSaving to {PENDING_POST_FILE.name}...")
    save_pending_post(caption, output_file)

    print("\n--- Summary ---")
    print(f"Clips:   {[c.name for c in local_clips]}")
    print(f"Hook:    {hook_text}")
    print(f"Output:  {output_path}")
    print(f"Caption: {caption[:100]}...")
    print("\nReady to post! Run: python3 post.py")


if __name__ == "__main__":
    main()
