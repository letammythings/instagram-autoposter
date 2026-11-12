"""
Nova — AI Executive Assistant for @your_account
==========================================
Nova researches top-performing content in your niche,
scans your Google Drive vault, classifies clips by content pillar,
matches them to trending strategy, and briefs compile.py on exactly
which clips to use and what story to tell.

Usage:
  python3 agent.py              # full run: research + scan + strategy + brief
  python3 agent.py --research   # niche research only
  python3 agent.py --scan       # scan & classify Drive vault clips
  python3 agent.py --strategy   # weekly strategy recommendations
  python3 agent.py --brief      # create compile brief from vault + strategy
"""

import os
import sys
import json
import base64
import argparse
import requests
from collections import Counter
from datetime import datetime
from pathlib import Path

import anthropic
from config import AGENT_MEMORY_FILE, BRAND_PROMPT_FILE, CREDENTIALS_FILE, ROOT_DIR, THUMBS_DIR
from google.oauth2 import service_account
from googleapiclient.discovery import build
from state import load_posted_log

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT             = ROOT_DIR
MEMORY_FILE      = AGENT_MEMORY_FILE
TRENDS_FILE      = ROOT_DIR / "trends.json"

# ── Drive config ──────────────────────────────────────────────────────────────
SCOPES      = ["https://www.googleapis.com/auth/drive.readonly"]
FOLDER_NAME = os.getenv("GOOGLE_DRIVE_FOLDER_NAME")
SUPPORTED_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif", ".mp4", ".mov"}

# ── Niches to research ────────────────────────────────────────────────────────
NICHES = {
    "vietnam_expat_lifestyle":      "Vietnam expat lifestyle content on Instagram and TikTok",
    "vietnamese_american_identity": "Vietnamese American identity and heritage content",
    "remote_work_abroad":           "remote work abroad and digital nomad lifestyle content",
    "southeast_asia_food":          "Southeast Asia food content, specifically Vietnamese street food",
    "faceless_lifestyle_accounts":  "faceless lifestyle Instagram accounts and content strategy",
}

CONTENT_PILLARS = ["food", "lifestyle", "heritage", "motivation", "scenery"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_brand_prompt():
    if not BRAND_PROMPT_FILE.exists():
        return ""
    return BRAND_PROMPT_FILE.read_text(encoding="utf-8")


def load_memory():
    if not MEMORY_FILE.exists():
        return {
            "research": {"last_updated": None, "niches": {}},
            "strategy": {"last_updated": None, "recommendations": [], "content_gaps": [], "trending_angles": [], "oversaturated": []},
            "vault":    {"last_scanned": None, "files": {}},
            "brief":    {},
        }
    with open(MEMORY_FILE) as f:
        return json.load(f)


def save_memory(memory):
    with open(MEMORY_FILE, "w") as f:
        json.dump(memory, f, indent=2)
    print(f"  Memory saved to {MEMORY_FILE.name}")

def now_iso():
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def strip_code_fences(text):
    if text.startswith("```"):
        lines = text.splitlines()
        return "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return text


# ── Google Drive helpers ──────────────────────────────────────────────────────

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


def list_all_drive_files(service, folder_id):
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
            files.extend(list_all_drive_files(service, item["id"]))
        else:
            files.append(item)
    return files



def download_thumbnail(thumbnail_url, file_name):
    THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    thumb_path = THUMBS_DIR / (Path(file_name).stem + ".jpg")
    if thumb_path.exists():
        return thumb_path
    resp = requests.get(thumbnail_url, timeout=15)
    resp.raise_for_status()
    with open(thumb_path, "wb") as f:
        f.write(resp.content)
    return thumb_path


# ── Research ──────────────────────────────────────────────────────────────────

def research_niche(client, niche_key, niche_description):
    print(f"  Researching: {niche_description}...")

    prompt = f"""You are Nova, an AI content strategist for @your_account — a faceless expat lifestyle creator living abroad. The account documents what it actually looks like to live between
two cultures, not just visit one. The account is faceless. The voice is funny, dry, specific,
and warm when it counts.

Research the current state of {niche_description} on Instagram and TikTok in 2026.

Return a JSON object with exactly these keys:

{{
  "top_hook_formulas": ["list of 3-5 hook patterns that perform best right now"],
  "caption_structures": ["list of 2-3 caption structures that drive engagement"],
  "top_content_pillars": ["list of 3-5 content themes that consistently perform"],
  "posting_frequency": "recommended posts per week",
  "hashtag_strategy": "brief description of what hashtag approach works best",
  "trending_themes_2026": ["list of 3-5 themes trending right now"],
  "oversaturated": ["list of 2-4 things that are overdone and should be avoided"],
  "unique_angles": ["list of 2-3 underexplored angles that could stand out"]
}}

Return ONLY the JSON object. Be specific and actionable."""

    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = strip_code_fences(msg.content[0].text.strip())
    try:
        findings = json.loads(raw)
        findings["researched_at"] = now_iso()
        return findings
    except json.JSONDecodeError:
        return {"raw": raw, "researched_at": now_iso()}


def run_research(client, memory):
    print("\n" + "=" * 50)
    print("  NOVA — NICHE RESEARCH")
    print("=" * 50)

    if "niches" not in memory["research"]:
        memory["research"]["niches"] = {}

    for niche_key, niche_description in NICHES.items():
        findings = research_niche(client, niche_key, niche_description)
        memory["research"]["niches"][niche_key] = findings

    memory["research"]["last_updated"] = now_iso()
    save_memory(memory)
    print("\n  Research complete. All 5 niches analyzed.")
    return memory


# ── Vault scan ────────────────────────────────────────────────────────────────

def classify_clip(client, file_name, thumb_path):
    """Classify a single clip into a content pillar using Claude vision."""
    with open(thumb_path, "rb") as f:
        img_data = base64.standard_b64encode(f.read()).decode("utf-8")

    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=16,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": img_data},
                },
                {
                    "type": "text",
                    "text": (
                        f"Filename: {file_name}\n\n"
                        "This is a thumbnail from an expat lifestyle Instagram "
                        "account (@your_account) in Da Nang, Vietnam.\n\n"
                        "Classify this clip into exactly ONE content pillar:\n"
                        "- food (Vietnamese food, restaurants, markets, cooking)\n"
                        "- lifestyle (cafes, remote work, daily life, streets, coffee)\n"
                        "- heritage (family, cultural moments, traditional settings, grandmother)\n"
                        "- motivation (mindset, reflection, personal growth, journaling)\n"
                        "- scenery (landscapes, nature, architecture, ocean, mountains)\n\n"
                        "Reply with ONLY the single word. Nothing else."
                    ),
                },
            ],
        }],
    )
    result = msg.content[0].text.strip().lower()
    return result if result in CONTENT_PILLARS else "lifestyle"


def run_scan(client, memory):
    """Scan Drive vault, classify all unposted clips, store in memory."""
    print("\n" + "=" * 50)
    print("  NOVA — VAULT SCAN")
    print("=" * 50)

    service = get_drive_service()
    folder_id = find_folder_id(service, FOLDER_NAME)
    all_files = list_all_drive_files(service, folder_id)
    posted_log = load_posted_log()

    unposted = [
        f for f in all_files
        if f["name"] not in posted_log
        and Path(f["name"]).suffix.lower() in SUPPORTED_TYPES
    ]

    print(f"  Found {len(unposted)} unposted files in vault.")

    if "vault" not in memory:
        memory["vault"] = {"last_scanned": None, "files": {}}

    vault_index = {}
    for f in unposted:
        name = f["name"]
        thumb_url = f.get("thumbnailLink")
        if not thumb_url:
            print(f"  Skipping {name} — no thumbnail.")
            continue

        print(f"  Classifying: {name}...", end=" ", flush=True)
        try:
            thumb_path = download_thumbnail(thumb_url, name)
            pillar = classify_clip(client, name, thumb_path)
            vault_index[name] = {
                "file_id": f["id"],
                "pillar": pillar,
                "name": name,
            }
            print(f"→ {pillar}")
        except Exception as e:
            print(f"→ error ({e})")

    memory["vault"] = {
        "last_scanned": now_iso(),
        "files": vault_index,
    }
    save_memory(memory)

    pillar_counts = Counter(v["pillar"] for v in vault_index.values())
    print(f"\n  Vault summary:")
    for pillar, count in sorted(pillar_counts.items()):
        print(f"    {pillar}: {count} clips")

    return memory


# ── Compile brief ─────────────────────────────────────────────────────────────

def run_brief(client, memory):
    """Match vault clips to strategy, create a brief for compile.py."""
    print("\n" + "=" * 50)
    print("  NOVA — COMPILE BRIEF")
    print("=" * 50)

    vault = memory.get("vault", {})
    strategy = memory.get("strategy", {})

    if not vault.get("files"):
        print("  No vault data found. Run python3 agent.py --scan first.")
        return memory

    files = vault["files"]
    by_pillar = {}
    for name, info in files.items():
        by_pillar.setdefault(info["pillar"], []).append(name)

    strategy_focus = strategy.get("week_summary", "No strategy set yet.")
    next_post = strategy.get("next_post_suggestion", "")
    recommendations = strategy.get("recommendations", [])

    prompt = f"""You are Nova, AI content strategist for @your_account.

This week's strategic focus: {strategy_focus}
Next post suggestion: {next_post}
Top recommendations: {json.dumps(recommendations[:3])}

Available unposted clips classified by content pillar:
{json.dumps(by_pillar, indent=2)}

Create a compile brief for the next Instagram Reel.
Select 5-7 clips total. Prioritize clips that match this week's strategy focus.
Mix pillars where it makes sense — a food post can have lifestyle establishing shots.

Return a JSON object:
{{
  "selected_clips": ["filename1.mp4", "filename2.mp4", ...],
  "theme": "one sentence describing the story this Reel tells",
  "mood": "energetic OR calm OR emotional OR funny",
  "hero_clip": "filename of the clip that should be longest",
  "order_logic": "brief explanation of why clips go in this order",
  "caption_angle": "which pillar and which hook style to use",
  "music_mood": "upbeat OR ambient OR emotional"
}}

Return ONLY the JSON. Only use filenames that appear in the available clips above."""

    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = strip_code_fences(msg.content[0].text.strip())
    try:
        brief = json.loads(raw)
        brief["created_at"] = now_iso()
        memory["brief"] = brief
        save_memory(memory)

        print(f"\n  THEME:         {brief.get('theme', '')}")
        print(f"  MOOD:          {brief.get('mood', '')}")
        print(f"  CAPTION ANGLE: {brief.get('caption_angle', '')}")
        print(f"  MUSIC MOOD:    {brief.get('music_mood', '')}")
        print(f"\n  SELECTED CLIPS ({len(brief.get('selected_clips', []))}):")
        for clip in brief.get("selected_clips", []):
            print(f"    • {clip}")
        print(f"\n  ORDER LOGIC: {brief.get('order_logic', '')}")
        print(f"\n  Ready. Run: python3 compile.py --nova")

    except json.JSONDecodeError:
        print(f"  Could not parse brief:\n{raw}")

    return memory


# ── Strategy ──────────────────────────────────────────────────────────────────

def run_strategy(client, memory):
    print("\n" + "=" * 50)
    print("  NOVA — WEEKLY CONTENT STRATEGY")
    print("=" * 50)

    research = memory.get("research", {})
    posted_log = load_posted_log()
    brand_prompt = load_brand_prompt()

    if not research.get("last_updated"):
        print("\n  No research data. Run python3 agent.py --research first.")
        return memory

    research_summary = json.dumps(research.get("niches", {}), indent=2)
    total_posts = len(posted_log)
    recent_posts = list(posted_log.keys())[-5:] if posted_log else []

    # Include vault data if available
    vault_summary = ""
    vault = memory.get("vault", {})
    if vault.get("files"):
        by_pillar = {}
        for name, info in vault["files"].items():
            by_pillar.setdefault(info["pillar"], []).append(name)
        pillar_counts = {k: len(v) for k, v in by_pillar.items()}
        vault_summary = f"\nVault inventory by pillar: {json.dumps(pillar_counts)}"

    # Include voice journal (what's actually been working)
    voice_journal_text = ""
    voice_journal = memory.get("voice_journal", [])
    if voice_journal:
        notes = "\n".join(f"- {n}" for n in voice_journal)
        voice_journal_text = f"\n\nWhat's been working (from real engagement data):\n{notes}"

    prompt = f"""You are Nova, AI content strategist for @your_account.

Brand context:
{brand_prompt[:2000]}

Niche research:
{research_summary[:3000]}

Posting history: {total_posts} total posts. Recent: {recent_posts}
{vault_summary}{voice_journal_text}

Give a weekly content strategy recommendation. Return JSON:

{{
  "week_summary": "one sentence summary of the strategic focus this week",
  "recommendations": [
    "specific actionable recommendation 1",
    "specific actionable recommendation 2",
    "specific actionable recommendation 3",
    "specific actionable recommendation 4",
    "specific actionable recommendation 5"
  ],
  "content_gaps": ["gap 1", "gap 2"],
  "trending_angles": ["angle 1", "angle 2", "angle 3"],
  "oversaturated": ["thing to avoid 1", "thing to avoid 2"],
  "next_post_suggestion": "specific suggestion — pillar, hook style, angle"
}}

Return ONLY the JSON. Be specific to @your_account's brand. Never suggest copying anyone."""

    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = strip_code_fences(msg.content[0].text.strip())
    try:
        strategy = json.loads(raw)
        strategy["generated_at"] = now_iso()
        memory["strategy"].update(strategy)
        memory["strategy"]["last_updated"] = now_iso()
        save_memory(memory)

        print(f"\n  FOCUS THIS WEEK: {strategy.get('week_summary', '')}")

        print("\n  RECOMMENDATIONS:")
        for i, rec in enumerate(strategy.get("recommendations", []), 1):
            print(f"  {i}. {rec}")

        print("\n  CONTENT GAPS TO FILL:")
        for gap in strategy.get("content_gaps", []):
            print(f"  • {gap}")

        print("\n  TRENDING ANGLES TO TRY:")
        for angle in strategy.get("trending_angles", []):
            print(f"  • {angle}")

        print("\n  WHAT TO AVOID:")
        for item in strategy.get("oversaturated", []):
            print(f"  ✗ {item}")

        print(f"\n  NEXT POST SUGGESTION:")
        print(f"  → {strategy.get('next_post_suggestion', '')}")

    except json.JSONDecodeError:
        print(f"\n  Strategy output:\n{raw}")

    return memory


# ── Main ──────────────────────────────────────────────────────────────────────

# ── Trend scraping ────────────────────────────────────────────────────────────

def run_trend_scrape(client, memory):
    """
    Use Claude's built-in web search to find trending Instagram Reel style parameters.
    Free — no Firecrawl credits needed. Uses the Anthropic web_search tool.
    """
    print("\n" + "=" * 50)
    print("  NOVA — TREND SCRAPE (Claude web search)")
    print("=" * 50)

    prompt = """You are Nova, AI content strategist for @your_account — a faceless expat lifestyle creator living abroad. The account is faceless, dry-funny, and documents real expat life.

Search the web right now for what's trending on Instagram Reels in 2026, specifically:
- Trending reel formats for food/lifestyle/travel content
- What hook styles are performing well on short-form video
- Average clip durations that get high completion rates
- Music energy trends (upbeat vs ambient vs emotional)
- Caption hook formulas that drive comments

After searching, extract concrete style parameters for @your_account to follow.

Return ONLY this JSON (no extra text):
{
  "avg_clip_seconds": 2.5,
  "transition_style": "jump_cut",
  "hook_text_formats": ["POV: you...", "the $X that...", "wait for it"],
  "music_energy": "high",
  "trending_formats": ["day-in-life", "price-reveal", "before-after"],
  "optimal_reel_length": 15,
  "caption_hook_style": "price-contrast"
}"""

    try:
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 4}],
            messages=[{"role": "user", "content": prompt}],
        )
        # Extract the final text response (after tool use)
        raw_text = ""
        for block in msg.content:
            if hasattr(block, "text"):
                raw_text = block.text.strip()
        raw = strip_code_fences(raw_text)
    except Exception as e:
        print(f"  Web search failed ({e}) — using Claude's training knowledge instead.")
        # Fallback: ask Claude without web search
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = strip_code_fences(msg.content[0].text.strip())

    try:
        trend_style = json.loads(raw)
        trend_style["scraped_at"] = now_iso()
        memory["trend_style"] = trend_style
        save_memory(memory)

        print(f"\n  TREND STYLE EXTRACTED:")
        print(f"  Avg clip duration: {trend_style.get('avg_clip_seconds')}s")
        print(f"  Transition style:  {trend_style.get('transition_style')}")
        print(f"  Music energy:      {trend_style.get('music_energy')}")
        print(f"  Optimal length:    {trend_style.get('optimal_reel_length')}s")
        print(f"  Trending formats:  {trend_style.get('trending_formats')}")
        print(f"  Hook formats:      {trend_style.get('hook_text_formats')}")
    except json.JSONDecodeError:
        print(f"  Could not parse trend style response:\n{raw}")

    return memory


def run_analyze_trends(client, memory):
    """Read trends.json, extract caption style patterns, save to agent_memory as observed_trends."""
    print("\n" + "=" * 50)
    print("  NOVA — ANALYZE OBSERVED TRENDS")
    print("=" * 50)

    if not TRENDS_FILE.exists():
        print("  trends.json not found. Add captions first:")
        print("  python3 add_trend.py 'caption here' --notes 'what you liked'")
        return memory

    try:
        with open(TRENDS_FILE, encoding="utf-8") as f:
            trends = json.load(f)
    except Exception as e:
        print(f"  Could not read trends.json: {e}")
        return memory

    if not trends:
        print("  trends.json is empty. Add some captions first:")
        print("  python3 add_trend.py 'caption here' --notes 'what you liked'")
        return memory

    print(f"  Analyzing {len(trends)} observed caption(s)...")

    captions_block = "\n\n---\n\n".join(
        f"Source: {t.get('source', 'unknown')}\n"
        f"Caption: {t.get('caption', '')}\n"
        f"Notes: {t.get('notes', '')}"
        for t in trends
    )

    prompt = f"""You are Nova, AI content strategist for @your_account — a faceless expat lifestyle creator living abroad. The account is faceless, dry-funny, specific, and warm.

She has collected these Instagram captions she admires. Analyze them and extract
the patterns that make them work, so she can apply similar techniques to her own content.

OBSERVED CAPTIONS:
{captions_block}

Return ONLY this JSON:
{{
  "hook_patterns": ["pattern 1 description", "pattern 2", "pattern 3"],
  "sentence_structures": ["structure 1", "structure 2"],
  "tone_elements": ["element 1", "element 2", "element 3"],
  "what_makes_them_work": "1-2 sentence summary of the common thread",
  "apply_to_account": ["specific suggestion 1 for @your_account", "suggestion 2", "suggestion 3"],
  "analyzed_at": ""
}}

Be specific and actionable. Focus on structure and style, not copying content."""

    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = strip_code_fences(msg.content[0].text.strip())
    try:
        patterns = json.loads(raw)
        patterns["analyzed_at"] = now_iso()
        patterns["caption_count"] = len(trends)
        memory["observed_trends"] = patterns
        save_memory(memory)

        print(f"\n  HOOK PATTERNS FOUND:")
        for p in patterns.get("hook_patterns", []):
            print(f"  • {p}")
        print(f"\n  TONE ELEMENTS:")
        for e in patterns.get("tone_elements", []):
            print(f"  • {e}")
        print(f"\n  WHAT MAKES THEM WORK:")
        print(f"  {patterns.get('what_makes_them_work', '')}")
        print(f"\n  APPLY TO @your_account:")
        for s in patterns.get("apply_to_account", []):
            print(f"  → {s}")
        print(f"\n  Saved to agent_memory.json[\"observed_trends\"]")
    except json.JSONDecodeError:
        print(f"  Could not parse pattern analysis:\n{raw}")

    return memory


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Nova — AI content strategist for @your_account")
    parser.add_argument("--research", action="store_true", help="Run niche research only")
    parser.add_argument("--scan",     action="store_true", help="Scan and classify Drive vault")
    parser.add_argument("--strategy", action="store_true", help="Run strategy only")
    parser.add_argument("--brief",    action="store_true", help="Create compile brief from vault + strategy")
    parser.add_argument("--trends",          action="store_true", help="Scrape web for trending Instagram style data")
    parser.add_argument("--analyze-trends",  action="store_true", help="Analyze captions in trends.json and extract style patterns")
    args = parser.parse_args()

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set in .env")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    memory = load_memory()

    if args.research:
        memory = run_research(client, memory)
    elif args.scan:
        memory = run_scan(client, memory)
    elif args.strategy:
        memory = run_strategy(client, memory)
    elif args.brief:
        memory = run_brief(client, memory)
    elif args.trends:
        memory = run_trend_scrape(client, memory)
    elif args.analyze_trends:
        memory = run_analyze_trends(client, memory)
    else:
        # Full run: research → trends → scan → strategy → brief
        memory = run_research(client, memory)
        memory = run_trend_scrape(client, memory)
        memory = run_scan(client, memory)
        memory = run_strategy(client, memory)
        memory = run_brief(client, memory)

    print("\n" + "=" * 50)
    print("  Nova complete.")
    print("=" * 50)


if __name__ == "__main__":
    main()
