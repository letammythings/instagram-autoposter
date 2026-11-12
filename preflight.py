import os
import tempfile
import datetime
from pathlib import Path

import requests
from config import CREDENTIALS_FILE, PENDING_POST_FILE
from state import load_pending_post

PASS = "  PASS"
FAIL = "  FAIL"

REQUIRED_VARS = [
    "ANTHROPIC_API_KEY",
    "INSTAGRAM_ACCESS_TOKEN",
    "INSTAGRAM_USER_ID",
    "INSTAGRAM_APP_ID",
    "INSTAGRAM_APP_SECRET",
    "GOOGLE_DRIVE_FOLDER_NAME",
    "EMAIL_SENDER",
    "EMAIL_PASSWORD",
    "EMAIL_RECEIVER",
    "CLOUDINARY_CLOUD_NAME",
    "CLOUDINARY_API_KEY",
    "CLOUDINARY_API_SECRET",
]

OPTIONAL_VARS = [
    "FIRECRAWL_API_KEY",   # needed for python3 agent.py --trends
    "TWELVELABS_API_KEY",  # needed for python3 process_raw.py
]


def check(label, fn):
    print(f"\n[{label}]")
    try:
        fn()
        print(PASS)
        return True
    except Exception as e:
        print(f"{FAIL}: {e}")
        return False


# ── 1. Environment variables ──────────────────────────────────────────────────
def test_env():
    missing = [v for v in REQUIRED_VARS if not os.getenv(v)]
    if missing:
        raise ValueError(f"Missing or empty: {', '.join(missing)}")
    print(f"  All {len(REQUIRED_VARS)} required variables present.")


# ── 2. Anthropic API ──────────────────────────────────────────────────────────
def test_anthropic():
    import anthropic
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=16,
        messages=[{"role": "user", "content": "Say hello in one word."}],
    )
    reply = msg.content[0].text.strip()
    print(f"  Claude replied: {reply}")


# ── 3. Google Drive ───────────────────────────────────────────────────────────
def test_google_drive():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    if not CREDENTIALS_FILE.exists():
        raise FileNotFoundError("credentials.json not found in project root.")

    creds = service_account.Credentials.from_service_account_file(
        str(CREDENTIALS_FILE), scopes=["https://www.googleapis.com/auth/drive.readonly"]
    )
    service = build("drive", "v3", credentials=creds)

    folder_name = os.getenv("GOOGLE_DRIVE_FOLDER_NAME")
    query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    result = service.files().list(q=query, fields="files(id, name)").execute()
    folders = result.get("files", [])
    if not folders:
        raise ValueError(f"Folder '{folder_name}' not found. Make sure the service account has been granted access.")
    print(f"  Found folder '{folder_name}' (id: {folders[0]['id']})")


# ── 4. Cloudinary ─────────────────────────────────────────────────────────────
def test_cloudinary():
    import shutil
    import subprocess
    import cloudinary
    import cloudinary.uploader

    cloudinary.config(
        cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
        api_key=os.getenv("CLOUDINARY_API_KEY"),
        api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    )

    # Upload a 1x1 white JPEG as a test image
    tiny_jpeg = (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
        b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
        b"\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\x1e"
        b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00"
        b"\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00"
        b"\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xc4\x00"
        b"\xb5\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04\x04\x00\x00"
        b"\x01}\x01\x02\x03\x00\x04\x11\x05\x12!1A\x06\x13Qa\x07\"q\x142\x81"
        b"\x91\xa1\x08#B\xb1\xc1\x15R\xd1\xf0$3br\x82\t\n\x16\x17\x18\x19"
        b"\x1a%&'()*456789:CDEFGHIJSTUVWXYZcdefghijstuvwxyz\x83\x84\x85\x86"
        b"\x87\x88\x89\x8a\x92\x93\x94\x95\x96\x97\x98\x99\x9a\xa2\xa3\xa4"
        b"\xa5\xa6\xa7\xa8\xa9\xaa\xb2\xb3\xb4\xb5\xb6\xb7\xb8\xb9\xba\xc2"
        b"\xc3\xc4\xc5\xc6\xc7\xc8\xc9\xca\xd2\xd3\xd4\xd5\xd6\xd7\xd8\xd9"
        b"\xda\xe1\xe2\xe3\xe4\xe5\xe6\xe7\xe8\xe9\xea\xf1\xf2\xf3\xf4\xf5"
        b"\xf6\xf7\xf8\xf9\xfa\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xfb\xd5"
        b"\xff\xd9"
    )

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        f.write(tiny_jpeg)
        tmp_path = f.name

    try:
        result = cloudinary.uploader.upload(tmp_path, resource_type="image", public_id="preflight_test")
        url = result["secure_url"]
        print(f"  Image upload OK: {url}")
        cloudinary.uploader.destroy("preflight_test")
        print(f"  Test image cleaned up.")
    finally:
        os.unlink(tmp_path)

    # Test video upload using FFmpeg to generate a tiny 1-second MP4
    if shutil.which("ffmpeg"):
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            tmp_video = f.name
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-f", "lavfi", "-i", "color=black:s=64x64:d=1",
                    "-c:v", "libx264", "-t", "1", "-pix_fmt", "yuv420p",
                    tmp_video,
                ],
                check=True,
                capture_output=True,
            )
            result = cloudinary.uploader.upload(
                tmp_video, resource_type="video", public_id="preflight_video_test"
            )
            print(f"  Video upload OK: {result['secure_url']}")
            cloudinary.uploader.destroy("preflight_video_test", resource_type="video")
            print(f"  Test video cleaned up.")
        finally:
            os.unlink(tmp_video)
    else:
        print("  Video upload skipped — ffmpeg not found. Install: brew install ffmpeg")


# ── 5. Instagram token identity + expiry ──────────────────────────────────────
def test_instagram_token():
    token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
    app_id = os.getenv("INSTAGRAM_APP_ID")
    app_secret = os.getenv("INSTAGRAM_APP_SECRET")

    # Basic identity check
    r = requests.get(
        "https://graph.facebook.com/v21.0/me",
        params={"fields": "id,name", "access_token": token},
    )
    data = r.json()
    if "error" in data:
        raise ValueError(data["error"]["message"])
    print(f"  Authenticated as: {data.get('name')} (id: {data.get('id')})")

    # Token expiry check via debug_token
    if app_id and app_secret:
        debug_r = requests.get(
            "https://graph.facebook.com/debug_token",
            params={
                "input_token": token,
                "access_token": f"{app_id}|{app_secret}",
            },
        )
        debug_data = debug_r.json().get("data", {})
        expires_at = debug_data.get("expires_at", 0)

        if expires_at == 0:
            print("  Token expiry: never (long-lived page/system token).")
        else:
            expiry_dt = datetime.datetime.utcfromtimestamp(expires_at)
            days_left = (expiry_dt - datetime.datetime.utcnow()).days
            expiry_str = expiry_dt.strftime("%Y-%m-%d")

            if days_left < 1:
                raise ValueError(
                    f"CRITICAL: Token expires TODAY ({expiry_str}). "
                    f"Run python3 refresh_token.py immediately!"
                )
            elif days_left < 7:
                print(
                    f"  WARNING: Token expires in {days_left} day(s) ({expiry_str}). "
                    f"Run python3 refresh_token.py soon!"
                )
            else:
                print(f"  Token expiry: {expiry_str} ({days_left} days remaining). OK.")
    else:
        print("  Token expiry: skipped (INSTAGRAM_APP_ID/SECRET not set).")


# ── 6. Caption length check ───────────────────────────────────────────────────
def test_caption_length():
    if not PENDING_POST_FILE.exists():
        print(f"  No {PENDING_POST_FILE.name} — skipping (run main.py to queue a post).")
        return
    pending = load_pending_post()
    if not pending:
        print("  pending_post.json is empty — skipping.")
        return
    caption = pending.get("caption", "")
    char_count = len(caption)
    if char_count > 2000:
        raise ValueError(
            f"Caption is {char_count} chars — exceeds 2000-char safe limit "
            f"(Instagram max: 2200). Run regenerate.py or post.py will truncate."
        )
    print(f"  Caption OK: {char_count}/2000 chars.")


# ── 7. Instagram account publish capability ───────────────────────────────────
def test_instagram_account():
    token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
    user_id = os.getenv("INSTAGRAM_USER_ID")
    r = requests.get(
        f"https://graph.facebook.com/v21.0/{user_id}",
        params={"fields": "id,username,name", "access_token": token},
    )
    data = r.json()
    if "error" in data:
        raise ValueError(data["error"]["message"])
    username = data.get("username") or data.get("name", "unknown")
    ig_id = data.get("id")
    print(f"  Instagram account: @{username} (id: {ig_id})")


# ── 8. moviepy + ffmpeg ───────────────────────────────────────────────────────
def test_moviepy():
    import shutil
    try:
        import moviepy.editor  # noqa: F401
        print("  moviepy importable.")
    except ImportError:
        raise RuntimeError("moviepy not installed. Run: pip install moviepy")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg binary not found. Install: apt install ffmpeg")
    if not shutil.which("ffprobe"):
        raise RuntimeError("ffprobe not found. Install: apt install ffmpeg")
    print("  ffmpeg and ffprobe binaries found.")


# ── 9. ffmpeg-python + ffmpeg binary ──────────────────────────────────────────
def test_ffmpeg():
    try:
        import ffmpeg
    except ImportError:
        raise RuntimeError(
            "ffmpeg-python not installed in this environment. "
            "Run: pip install ffmpeg-python"
        )
    import shutil
    if not shutil.which("ffmpeg"):
        raise RuntimeError(
            "ffmpeg binary not found. Install with: brew install ffmpeg"
        )
    print("  ffmpeg-python importable. ffmpeg binary found.")


# ── Run all checks ────────────────────────────────────────────────────────────
def main():
    print("=" * 50)
    print("  PREFLIGHT CHECK — ig-autoposter")
    print("=" * 50)

    results = [
        check("1. Environment variables", test_env),
        check("2. Anthropic API", test_anthropic),
        check(f"3. Google Drive + {os.getenv('GOOGLE_DRIVE_FOLDER_NAME', 'vault')} folder", test_google_drive),
        check("4. Cloudinary upload", test_cloudinary),
        check("5. Instagram token identity", test_instagram_token),
        check("6. Caption length", test_caption_length),
        check("7. Instagram account type", test_instagram_account),
        check("8. moviepy + ffmpeg", test_moviepy),
        check("9. ffmpeg-python + binary", test_ffmpeg),
    ]

    passed = sum(results)
    total = len(results)

    print("\n" + "=" * 50)
    if passed == total:
        print(f"  ALL {total} CHECKS PASSED — ready to run main.py, post.py, and compile.py")

    else:
        print(f"  {passed}/{total} passed — fix the FAILs above before running.")
    print("=" * 50)


if __name__ == "__main__":
    main()
