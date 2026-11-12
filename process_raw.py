#!/usr/bin/env python3
"""
process_raw.py — Automated raw video processor using Twelve Labs AI.

Flow:
  1. Scan all subfolders inside the vault for long raw videos
  2. Download each to temp/raw/
  3. Check duration — skip videos under 30s (already-processed clips)
  4. Upload to Twelve Labs for AI indexing
  5. Query for food, lifestyle, and scenic moments
  6. Cut best segments (2–8s) with ffmpeg
  7. Upload clips back to the SAME subfolder (preserving location context for captions)
  8. Mark raw video as processed — idempotent, safe to re-run

Usage:
    python3 process_raw.py                    # process all subfolders
    python3 process_raw.py --folder "hoi an"  # process one subfolder only
    python3 process_raw.py --dry-run          # list unprocessed videos, no action
"""

import json
import os
import subprocess
import time
from pathlib import Path

import requests
from config import CREDENTIALS_FILE, TEMP_DIR
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

SCOPES        = ["https://www.googleapis.com/auth/drive"]
FOLDER_NAME   = os.getenv("GOOGLE_DRIVE_FOLDER_NAME")
TL_BASE       = "https://api.twelvelabs.io/v1.3"
TL_INDEX_NAME = FOLDER_NAME or "ig-vault"
STATE_FILE    = Path(__file__).resolve().parent / "raw_processed.json"
RAW_DIR       = TEMP_DIR / "raw"
CLIPS_DIR     = TEMP_DIR / "clips"
VIDEO_EXTS    = {".mp4", ".mov", ".m4v"}

MIN_RAW_DURATION    = 30.0  # seconds — shorter files are already clips, skip them
SEARCH_QUERIES = [
    # Food — sensory and specific
    "Vietnamese food steaming hot close up just served",
    "street vendor cooking with visible flame wok action",
    "colorful noodle bowl soup garnish detail overhead",
    "hands holding food cup close texture detail",
    # Cafe + lifestyle — aesthetic
    "iced coffee poured condensation glass slow motion",
    "cozy cafe interior natural light window plants bokeh",
    # Scenery — motion and light
    "golden hour warm light street water reflection",
    "motorbikes flowing through narrow alley organic motion",
    "ocean waves crashing beach coastline",
    "rain puddle reflection street wet pavement",
    # Atmosphere and texture
    "lanterns lights bokeh night scene Vietnam",
    "market stall colorful produce overhead flat lay",
    "feet walking tiled floor market alleyway texture",
    "sunrise mist mountains fog landscape Vietnam",
    "city skyline river bridge golden hour Da Nang",
]
MIN_SCORE           = 0.82
CLIP_PAD_SEC        = 0.3
CLIP_MIN_DUR        = 2.0
CLIP_MAX_DUR        = 7.0
MAX_CLIPS_PER_VIDEO = 12


# ── State ──────────────────────────────────────────────────────────────────────

def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"index_id": None, "processed": {}}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ── Google Drive ───────────────────────────────────────────────────────────────

def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        str(CREDENTIALS_FILE), scopes=SCOPES
    )
    return build("drive", "v3", credentials=creds)


def find_folder_id(service, name, parent_id=None):
    q = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        q += f" and '{parent_id}' in parents"
    result = service.files().list(q=q, fields="files(id,name)").execute()
    folders = result.get("files", [])
    if not folders:
        raise ValueError(f"Drive folder '{name}' not found.")
    return folders[0]["id"]


def list_vault_subfolders(service, vault_id):
    result = service.files().list(
        q=f"'{vault_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
        fields="files(id,name)",
        pageSize=100,
    ).execute()
    return result.get("files", [])


def list_drive_videos(service, folder_id):
    result = service.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        fields="files(id,name,size)",
        pageSize=100,
    ).execute()
    return [
        f for f in result.get("files", [])
        if Path(f["name"]).suffix.lower() in VIDEO_EXTS
    ]


def download_file(service, file_id, dest_path):
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path.exists():
        print(f"  Cached: {dest_path.name}")
        return dest_path
    request = service.files().get_media(fileId=file_id)
    with open(dest_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status:
                print(f"  {dest_path.name}: {int(status.progress() * 100)}%", end="\r")
    print()
    return dest_path


def upload_clip_to_subfolder(service, clip_path, folder_id):
    import mimetypes
    mime = mimetypes.guess_type(str(clip_path))[0] or "video/mp4"
    media = MediaFileUpload(str(clip_path), mimetype=mime, resumable=True)
    meta = {"name": clip_path.name, "parents": [folder_id]}
    result = service.files().create(body=meta, media_body=media, fields="id").execute()
    return result["id"]


# ── Video duration ─────────────────────────────────────────────────────────────

def get_video_duration(video_path):
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(video_path)],
        capture_output=True, text=True,
    )
    try:
        data = json.loads(result.stdout)
        return float(data.get("format", {}).get("duration", 0))
    except (json.JSONDecodeError, ValueError):
        return 0.0


# ── Twelve Labs ────────────────────────────────────────────────────────────────

def _tl_headers(json=False):
    h = {"x-api-key": os.getenv("TWELVELABS_API_KEY")}
    if json:
        h["Content-Type"] = "application/json"
    return h


def get_or_create_index(state):
    if state.get("index_id"):
        r = requests.get(f"{TL_BASE}/indexes/{state['index_id']}", headers=_tl_headers())
        if r.status_code == 200:
            print(f"  Using existing TL index: {state['index_id']}")
            return state["index_id"]
        print(f"  Index not found (status {r.status_code}), creating new...")

    print("  Creating Twelve Labs index...")
    r = requests.post(f"{TL_BASE}/indexes", headers=_tl_headers(json=True), json={
        "index_name": TL_INDEX_NAME,
        "models": [
            {
                "model_name": "marengo3.0",
                "model_options": ["visual", "audio"],
            }
        ],
    })
    if not r.ok:
        print(f"  TL error {r.status_code}: {r.text[:300]}")
    r.raise_for_status()
    data = r.json()
    index_id = data.get("_id") or data.get("id")
    state["index_id"] = index_id
    save_state(state)
    print(f"  Created index: {index_id}")
    return index_id


def upload_to_twelve_labs(index_id, video_path):
    print(f"  Uploading {video_path.name} to Twelve Labs...")
    with open(video_path, "rb") as f:
        r = requests.post(
            f"{TL_BASE}/tasks",
            headers=_tl_headers(),
            data={"index_id": index_id},
            files={"video_file": (video_path.name, f, "video/mp4")},
        )
    r.raise_for_status()
    tdata = r.json()
    task_id = tdata.get("_id") or tdata.get("id")
    print(f"  Task: {task_id}")
    return task_id


def wait_for_indexing(task_id, timeout=1200):
    print(f"  Waiting for indexing (up to {timeout // 60}min)...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = requests.get(f"{TL_BASE}/tasks/{task_id}", headers=_tl_headers())
        r.raise_for_status()
        data   = r.json()
        raw_status = data.get("status") or ""
        status = raw_status.strip().lower()
        print(f"  Status: {raw_status}          ", end="\r")
        if status == "ready":
            video_id = data.get("video_id")
            print(f"\n  Indexed. video_id={video_id}")
            return video_id
        if status in ("failed", "error"):
            raise RuntimeError(f"Twelve Labs indexing failed: {data}")
        time.sleep(15)
    raise TimeoutError("Twelve Labs indexing timed out")


def search_moments(index_id, video_id):
    all_clips = []
    seen_keys = set()

    for query in SEARCH_QUERIES:
        print(f"  Searching: '{query}'")
        r = requests.post(f"{TL_BASE}/search", headers=_tl_headers(json=True), json={
            "index_id":       index_id,
            "query_text":     query,
            "search_options": ["visual"],
            "filter":         json.dumps({"id": [video_id]}),
            "page_limit":     5,
        })
        if r.status_code != 200:
            print(f"    Search error {r.status_code}: {r.text[:120]}")
            continue

        for clip in r.json().get("data", []):
            score = clip.get("score", 0)
            if score < MIN_SCORE:
                continue
            start = round(float(clip.get("start", 0)), 2)
            end   = round(float(clip.get("end", 0)), 2)
            key   = (round(start), round(end))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            all_clips.append({"start": start, "end": end, "score": score, "query": query})

    all_clips.sort(key=lambda c: c["score"], reverse=True)
    print(f"  Found {len(all_clips)} unique candidate moments")
    return all_clips


def merge_overlapping(clips):
    if not clips:
        return []
    clips  = sorted(clips, key=lambda c: c["start"])
    merged = [clips[0].copy()]
    for c in clips[1:]:
        last = merged[-1]
        if c["start"] <= last["end"] + 1.0:
            last["end"]   = max(last["end"], c["end"])
            last["score"] = max(last["score"], c["score"])
        else:
            merged.append(c.copy())
    return merged


# ── ffmpeg ─────────────────────────────────────────────────────────────────────

def cut_clip(source, start, end, out_path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    t_start  = max(0.0, start - CLIP_PAD_SEC)
    duration = min((end - start) + CLIP_PAD_SEC * 2, CLIP_MAX_DUR)

    # Try stream copy first (fast, no quality loss)
    subprocess.run([
        "ffmpeg", "-y", "-ss", str(t_start), "-i", str(source),
        "-t", str(duration), "-c", "copy", "-avoid_negative_ts", "make_zero",
        str(out_path),
    ], capture_output=True)

    if out_path.exists() and out_path.stat().st_size > 10_000:
        return True

    # Fallback: re-encode
    subprocess.run([
        "ffmpeg", "-y", "-ss", str(t_start), "-i", str(source),
        "-t", str(duration), "-c:v", "libx264", "-crf", "20", "-preset", "fast",
        "-c:a", "aac", str(out_path),
    ], capture_output=True)

    return out_path.exists() and out_path.stat().st_size > 10_000


# ── Processing ─────────────────────────────────────────────────────────────────

def process_video(video_file, service, subfolder_id, index_id, state):
    name     = video_file["name"]
    drive_id = video_file["id"]
    stem     = Path(name).stem
    size_mb  = int(video_file.get("size", 0)) / (1024 * 1024)
    print(f"\n  Processing: {name} ({size_mb:.0f}MB)")

    # Download — use drive_id in filename to avoid collisions between same-named files
    local_path = RAW_DIR / f"{drive_id}_{name}"
    download_file(service, drive_id, local_path)

    # Duration check — skip files that are already short clips
    duration = get_video_duration(local_path)
    if duration < MIN_RAW_DURATION:
        print(f"  Too short ({duration:.1f}s) — already a processed clip, skipping.")
        state["processed"][drive_id] = {"name": name, "skipped": "too_short", "duration": round(duration, 1)}
        save_state(state)
        local_path.unlink(missing_ok=True)
        return

    # Upload to TL (idempotent — skip if already indexed)
    entry = state["processed"].get(drive_id, {})
    if entry.get("video_id"):
        video_id = entry["video_id"]
        print(f"  Already indexed: {video_id}")
    else:
        task_id  = upload_to_twelve_labs(index_id, local_path)
        video_id = wait_for_indexing(task_id)
        state["processed"][drive_id] = {
            "name":           name,
            "drive_id":       drive_id,
            "task_id":        task_id,
            "video_id":       video_id,
            "clips_uploaded": [],
            "processed_at":   None,
        }
        save_state(state)

    # Search + merge + filter
    raw_clips = search_moments(index_id, video_id)
    clips     = merge_overlapping(raw_clips)
    clips     = [c for c in clips if CLIP_MIN_DUR <= (c["end"] - c["start"]) <= CLIP_MAX_DUR]
    print(f"  {len(clips)} usable clips after merge + duration filter")

    if not clips:
        print("  No usable moments found. Marking as processed.")
        state["processed"][drive_id]["processed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        save_state(state)
        local_path.unlink(missing_ok=True)
        return

    # Cut + upload back to the same subfolder
    uploaded = []
    for i, clip in enumerate(clips[:MAX_CLIPS_PER_VIDEO]):
        clip_name = f"{stem}_clip{i + 1:02d}_{int(clip['start'])}s.mp4"
        clip_path = CLIPS_DIR / clip_name
        dur       = clip["end"] - clip["start"]
        print(f"  [{i + 1}] {clip['start']:.1f}s–{clip['end']:.1f}s ({dur:.1f}s, score={clip['score']:.2f})")

        if not cut_clip(local_path, clip["start"], clip["end"], clip_path):
            print(f"    Cut failed, skipping.")
            continue

        mb = clip_path.stat().st_size / (1024 * 1024)
        print(f"    Cut OK ({mb:.1f}MB). Uploading to subfolder...")
        file_id = upload_clip_to_subfolder(service, clip_path, subfolder_id)
        uploaded.append(clip_name)
        print(f"    Uploaded: {clip_name} (id: {file_id})")
        clip_path.unlink(missing_ok=True)

    state["processed"][drive_id]["clips_uploaded"] = uploaded
    state["processed"][drive_id]["processed_at"]   = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save_state(state)
    print(f"  Done: {len(uploaded)} clips uploaded to subfolder.")
    local_path.unlink(missing_ok=True)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder", type=str, default=None, help="Process only this subfolder (e.g. 'hoi an')")
    parser.add_argument("--dry-run", action="store_true", help="List unprocessed videos without acting")
    args = parser.parse_args()

    if not args.dry_run and not os.getenv("TWELVELABS_API_KEY"):
        print("TWELVELABS_API_KEY not set. Add it to .env or ~/.env.master (loaded automatically by config).")
        return

    state = load_state()

    print("Connecting to Google Drive...")
    service  = get_drive_service()
    vault_id = find_folder_id(service, FOLDER_NAME)

    if args.folder:
        subfolder_id = find_folder_id(service, args.folder, parent_id=vault_id)
        subfolders = [{"id": subfolder_id, "name": args.folder}]
    else:
        print("Scanning vault subfolders...")
        subfolders = list_vault_subfolders(service, vault_id)
        if not subfolders:
            print("No subfolders found in vault.")
            return
        print(f"  Found {len(subfolders)} subfolder(s): {[s['name'] for s in subfolders]}")

    total_unprocessed = 0
    for subfolder in subfolders:
        sf_name = subfolder["name"]
        sf_id   = subfolder["id"]
        print(f"\n[{sf_name}]")

        all_videos = list_drive_videos(service, sf_id)

        # Deduplicate by filename — if two files share a name, keep only the first
        seen_names: set = set()
        deduped = []
        for v in all_videos:
            if v["name"] in seen_names:
                print(f"  Skipping duplicate filename: {v['name']}")
            else:
                seen_names.add(v["name"])
                deduped.append(v)
        all_videos = deduped

        unprocessed = [
            v for v in all_videos
            if not state["processed"].get(v["id"], {}).get("processed_at")
            and not state["processed"].get(v["id"], {}).get("skipped")
        ]
        print(f"  {len(all_videos)} unique video(s), {len(unprocessed)} to process.")
        total_unprocessed += len(unprocessed)

        if args.dry_run:
            for v in unprocessed:
                mb = int(v.get("size", 0)) / (1024 * 1024)
                print(f"  → {v['name']} ({mb:.0f}MB)")
            continue

        if not unprocessed:
            continue

        index_id = get_or_create_index(state)

        for video_file in unprocessed:
            try:
                process_video(video_file, service, sf_id, index_id, state)
            except Exception as e:
                print(f"  ERROR on {video_file['name']}: {e}")

    if args.dry_run:
        print(f"\n[DRY RUN] {total_unprocessed} unprocessed video(s) across all subfolders.")
        return

    total_clips = sum(
        len(v.get("clips_uploaded", []))
        for v in state["processed"].values()
        if isinstance(v, dict)
    )
    print(f"\n=== Done. Total clips in vault from raw processor: {total_clips} ===")


if __name__ == "__main__":
    main()
