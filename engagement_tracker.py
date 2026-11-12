"""
engagement_tracker.py — Poll Instagram Graph API for post engagement.

Run daily (cron: 2am UTC). Finds posts from ~24h ago that have a post_id
but no engagement data yet, fetches like/comment counts, and updates:
  - posted_log.json: adds engagement block to each entry
  - agent_memory.json: recomputes caption_style A/B performance stats
"""

import os
import datetime
import json
import requests
import anthropic

from config import POSTED_LOG_FILE, AGENT_MEMORY_FILE
from state import load_posted_log, save_posted_log

GRAPH_API_BASE = "https://graph.facebook.com/v21.0"
TRACK_WINDOW_MIN_H = 20
TRACK_WINDOW_MAX_H = 50


def load_agent_memory():
    if not AGENT_MEMORY_FILE.exists():
        return {}
    with open(AGENT_MEMORY_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_agent_memory(memory):
    with open(AGENT_MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(memory, f, indent=2)


def fetch_engagement(post_id, access_token):
    r = requests.get(
        f"{GRAPH_API_BASE}/{post_id}",
        params={"fields": "like_count,comments_count", "access_token": access_token},
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    return data.get("like_count", 0), data.get("comments_count", 0)


def recompute_caption_style_stats(posted_log):
    stats = {"short": {"posts": 0, "total_likes": 0, "total_comments": 0},
             "long":  {"posts": 0, "total_likes": 0, "total_comments": 0}}
    for entry in posted_log.values():
        if not isinstance(entry, dict):
            continue
        style = entry.get("caption_style")
        eng = entry.get("engagement")
        if style not in stats or not eng:
            continue
        stats[style]["posts"] += 1
        stats[style]["total_likes"] += eng.get("likes", 0)
        stats[style]["total_comments"] += eng.get("comments", 0)

    result = {}
    for style, s in stats.items():
        n = s["posts"]
        result[style] = {
            "posts": n,
            "avg_likes": round(s["total_likes"] / n, 2) if n else 0.0,
            "avg_comments": round(s["total_comments"] / n, 2) if n else 0.0,
        }
    return result


def update_voice_journal(posted_log, memory):
    """
    Find the top 5 posts by likes, extract what made their captions work,
    and save 3-5 bullet notes to agent_memory["voice_journal"].
    Runs only when there are at least 3 posts with engagement data.
    """
    posts_with_engagement = [
        (fname, entry) for fname, entry in posted_log.items()
        if isinstance(entry, dict) and entry.get("engagement") and entry.get("caption_style")
    ]
    if len(posts_with_engagement) < 3:
        return

    posts_with_engagement.sort(key=lambda x: x[1]["engagement"].get("likes", 0), reverse=True)
    top_posts = posts_with_engagement[:5]

    captions_block = "\n\n".join(
        f"File: {fname}\nLikes: {entry['engagement']['likes']}  "
        f"Comments: {entry['engagement']['comments']}\n"
        f"Style: {entry.get('caption_style', '?')}  Pillar: {entry.get('content_type', '?')}"
        for fname, entry in top_posts
    )

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return

    client = anthropic.Anthropic(api_key=api_key)
    try:
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=512,
            messages=[{"role": "user", "content": (
                "You are Nova, content strategist for @your_account (expat lifestyle).\n\n"
                "These are the top-performing posts by likes so far:\n\n"
                f"{captions_block}\n\n"
                "Write 3-5 short, specific, actionable bullet notes about what's working — "
                "patterns in style, pillar, or approach that should be repeated. "
                "Each note max 15 words. Be specific, not generic.\n\n"
                "Reply with ONLY a JSON array of strings: [\"note 1\", \"note 2\", ...]"
            )}],
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = "\n".join(raw.splitlines()[1:-1])
        notes = json.loads(raw)
        if isinstance(notes, list):
            memory.setdefault("voice_journal", [])
            memory["voice_journal"] = notes[:5]
            print(f"  Voice journal updated: {len(notes)} notes from top {len(top_posts)} posts.")
    except Exception as e:
        print(f"  Voice journal update failed (non-fatal): {e}")


def main():
    access_token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
    if not access_token:
        raise ValueError("INSTAGRAM_ACCESS_TOKEN not set")

    now = datetime.datetime.utcnow()
    posted_log = load_posted_log()
    entry_count = len(posted_log)

    # Safety: if load returned an empty dict but the file exists, refuse to overwrite
    # (protects against wiping the log if the file was temporarily unreadable)
    if entry_count == 0:
        from config import POSTED_LOG_FILE
        if POSTED_LOG_FILE.exists() and POSTED_LOG_FILE.stat().st_size > 10:
            print(f"  WARNING: posted_log.json exists ({POSTED_LOG_FILE.stat().st_size}B) but loaded 0 entries — skipping to avoid data loss.")
            return

    print(f"  Loaded {entry_count} posted log entries.")
    tracked = 0
    skipped = 0

    for file_name, entry in posted_log.items():
        if not isinstance(entry, dict):
            continue
        post_id = entry.get("post_id")
        if not post_id:
            skipped += 1
            continue
        if "engagement" in entry:
            continue  # already tracked

        posted_at_str = entry.get("posted_at", "")
        try:
            posted_at = datetime.datetime.fromisoformat(posted_at_str.rstrip("Z"))
        except (ValueError, AttributeError):
            skipped += 1
            continue

        age_h = (now - posted_at).total_seconds() / 3600
        if not (TRACK_WINDOW_MIN_H <= age_h <= TRACK_WINDOW_MAX_H):
            skipped += 1
            continue

        print(f"Tracking: {file_name} (post_id={post_id}, age={age_h:.1f}h)")
        try:
            likes, comments = fetch_engagement(post_id, access_token)
            entry["engagement"] = {
                "likes": likes,
                "comments": comments,
                "tracked_at": now.isoformat() + "Z",
            }
            tracked += 1
            print(f"  likes={likes}  comments={comments}")
        except Exception as e:
            print(f"  Failed to fetch engagement for {file_name}: {e}")

    if tracked > 0:
        save_posted_log(posted_log)
    print(f"\nTracked {tracked} post(s). Skipped {skipped}.")

    print("Updating caption_style performance stats in agent_memory.json...")
    memory = load_agent_memory()
    perf = memory.setdefault("performance", {})
    perf["caption_style"] = recompute_caption_style_stats(posted_log)
    perf["caption_style_updated_at"] = now.isoformat() + "Z"
    save_agent_memory(memory)
    print(f"  Stats: {perf['caption_style']}")

    print("Updating voice journal from top-performing posts...")
    update_voice_journal(posted_log, memory)
    save_agent_memory(memory)


if __name__ == "__main__":
    main()
