"""
locations.py — Location context loader for @your_account caption generation.

Maps Google Drive subfolder names to rich location descriptions that Claude
uses to write accurate, place-specific captions and hooks.

To add a new location: edit location_map.json and add an entry whose key
matches your Drive subfolder name (lowercase).
"""

import json
from config import LOCATION_MAP_FILE


def get_location_context(folder_name) -> str:
    """
    Returns a formatted location context string for Claude's caption prompt.
    Falls back gracefully if the folder isn't in the map.
    """
    if not folder_name:
        return ""

    try:
        with open(LOCATION_MAP_FILE) as f:
            location_map = json.load(f)
    except Exception:
        return f"LOCATION CONTEXT: Content filmed at {folder_name.title()}.\n\n"

    key = folder_name.strip().lower()
    loc = location_map.get(key)

    if not loc:
        # Not in the map — use the folder name as a basic hint
        return f"LOCATION CONTEXT: Content filmed at {folder_name.title()}, Vietnam. Reference this place naturally in the caption.\n\n"

    name        = loc.get("name", folder_name.title())
    city        = loc.get("city", "")
    country     = loc.get("country", "Vietnam")
    description = loc.get("description", "")
    known_for   = loc.get("known_for", [])
    vibes       = loc.get("vibes", [])

    parts = [f"LOCATION CONTEXT: {name}"]
    if city and city != name:
        parts[0] += f", {city}"
    parts[0] += f", {country}."

    if description:
        parts.append(description)
    if known_for:
        parts.append(f"Known for: {', '.join(known_for)}.")
    if vibes:
        parts.append(f"Vibe: {', '.join(vibes)}.")

    parts.append("Reference this place and these specific details naturally in the caption — don't just name-drop it, weave it in.")

    return "\n".join(parts) + "\n\n"
