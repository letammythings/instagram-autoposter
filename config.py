from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent

# Load secrets for cron/minimal shells: repo .env (local dev) then ~/.env.master (VPS).
# Existing OS env vars win (default override=False).
load_dotenv(ROOT_DIR / ".env")
load_dotenv(Path.home() / ".env.master")

CREDENTIALS_FILE = ROOT_DIR / "credentials.json"
BRAND_PROMPT_FILE = ROOT_DIR / "Brand_Prompt.txt"
POSTED_LOG_FILE = ROOT_DIR / "posted_log.json"
PENDING_POST_FILE = ROOT_DIR / "pending_post.json"
AGENT_MEMORY_FILE = ROOT_DIR / "agent_memory.json"
LOCATION_MAP_FILE = ROOT_DIR / "location_map.json"

TEMP_DIR = ROOT_DIR / "temp"
THUMBS_DIR = TEMP_DIR / "thumbs"
OUTPUT_FILE = TEMP_DIR / "compiled_reel.mp4"
MUSIC_DIR = ROOT_DIR / "music"
