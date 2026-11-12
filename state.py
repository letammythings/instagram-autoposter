import json
import os
import tempfile

from config import PENDING_POST_FILE, POSTED_LOG_FILE


def load_json(path, default):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def save_json(path, data):
    # Atomic write: write to temp file then rename so we never corrupt the target
    dir_ = path.parent
    dir_.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_posted_log():
    return load_json(POSTED_LOG_FILE, {})


def save_posted_log(posted_log):
    save_json(POSTED_LOG_FILE, posted_log)


def load_pending_post():
    return load_json(PENDING_POST_FILE, {})


def save_pending_post_data(pending):
    save_json(PENDING_POST_FILE, pending)


def clear_pending_post():
    save_json(PENDING_POST_FILE, {})
