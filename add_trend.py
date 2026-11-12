#!/usr/bin/env python3
"""
add_trend.py — Save a trending Instagram caption to trends.json for Nova to learn from.

Usage:
    python3 add_trend.py "caption text here"
    python3 add_trend.py "caption text here" --notes "love this hook style"
    python3 add_trend.py "caption text here" --source "faceless food account, 80k followers"
    python3 add_trend.py "caption text here" --source "expat account" --notes "great price contrast hook"

After adding a few captions, run:
    python3 agent.py --analyze-trends
"""

import argparse
import json
import datetime
from pathlib import Path

from config import ROOT_DIR

TRENDS_FILE = ROOT_DIR / "trends.json"


def load_trends():
    if not TRENDS_FILE.exists():
        return []
    try:
        with open(TRENDS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_trends(trends):
    with open(TRENDS_FILE, "w", encoding="utf-8") as f:
        json.dump(trends, f, indent=2, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(description="Add a trending Instagram caption to trends.json")
    parser.add_argument("caption", help="The caption text to save")
    parser.add_argument("--notes", default="", help="What you liked about it (hook style, structure, tone, etc.)")
    parser.add_argument("--source", default="", help="Description of the account or post (optional)")
    args = parser.parse_args()

    trends = load_trends()
    entry = {
        "source": args.source,
        "caption": args.caption,
        "notes": args.notes,
        "added_at": datetime.date.today().isoformat(),
    }
    trends.append(entry)
    save_trends(trends)

    print(f"Saved. trends.json now has {len(trends)} caption(s).")
    print(f"Run: python3 agent.py --analyze-trends  to extract patterns into Nova.")


if __name__ == "__main__":
    main()
