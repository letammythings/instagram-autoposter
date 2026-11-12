#!/bin/bash
# security_check.sh — Scans only the files that just synced to VPS.
# Reads a list of relative file paths from stdin (output of rsync --out-format="%n").
# Checks each file for .env secret values that shouldn't be in source code.
# Alerts via macOS notification + terminal warning on next prompt.

PROJECT_DIR="$HOME/ig-auto"
ENV_FILE="$PROJECT_DIR/.env"
WARNINGS_FILE="/tmp/account_security_warnings.txt"

# Only scan these file types
SCAN_EXTENSIONS="py|sh|txt|md"

# Clear previous warnings
> "$WARNINGS_FILE"

# Load actual secret values from .env (skip comments, empty lines, short values)
declare -a SECRETS
while IFS= read -r line; do
  [[ "$line" =~ ^#.*$ || -z "$line" ]] && continue
  value="${line#*=}"
  # Skip values under 10 chars — too short to be meaningful secrets
  [[ ${#value} -lt 10 ]] && continue
  SECRETS+=("$value")
done < "$ENV_FILE"

FOUND=0

while IFS= read -r rel_path; do
  # Skip directories, .env itself, and non-target file types
  [[ "$rel_path" == */ ]] && continue
  [[ "$rel_path" == ".env" || "$rel_path" == ".env.example" ]] && continue
  [[ ! "$rel_path" =~ \.($SCAN_EXTENSIONS)$ ]] && continue

  full_path="$PROJECT_DIR/$rel_path"
  [[ ! -f "$full_path" ]] && continue

  # Check if any .env value appears in this file
  for secret in "${SECRETS[@]}"; do
    if grep -qF "$secret" "$full_path" 2>/dev/null; then
      echo "⚠️  SECRET LEAK: $rel_path contains a value from .env" >> "$WARNINGS_FILE"
      FOUND=1
      break
    fi
  done
done

# Send macOS notification + sound if anything was found
if [ "$FOUND" -eq 1 ]; then
  osascript -e 'display notification "A synced file may contain a leaked secret. Check your terminal." with title "@your_account Security Alert" sound name "Basso"' 2>/dev/null
fi
