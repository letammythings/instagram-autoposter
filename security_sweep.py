#!/usr/bin/env python3
"""
security_sweep.py — Weekly full security scan for @your_account autoposter.

Runs on the VPS every Sunday. Checks:
  1. Secret leaks   — .env values found in source files
  2. File perms     — .env and credentials.json must be 600
  3. Network        — what ports are exposed to the internet
  4. Approver server — is it running? is it locked down?
  5. SSH config     — password auth, root login
  6. Dependencies   — known CVEs via pip-audit
  7. Cron integrity — show all jobs so you can spot anything unexpected
  8. Unexpected files — anything that shouldn't be in the project

Emails an HTML report to EMAIL_RECEIVER when done.
"""

import json
import os
import re
import smtplib
import subprocess
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import config  # noqa: F401 — loads env from config.py before os.getenv

PROJECT_DIR = Path(__file__).parent
ENV_FILE    = PROJECT_DIR / ".env"

SCAN_EXTENSIONS  = {".py", ".sh", ".md", ".txt"}
SKIP_FILES       = {
    ".env", ".env.example", "token_log.txt",
    "posted_log.json", "pending_post.json", "agent_memory.json",
}
SKIP_DIRS = {".venv", "__pycache__", "logs", "temp", ".git", ".claude"}

# Only these env vars are actual secrets — config values like folder names are excluded
CREDENTIAL_VARS = {
    "ANTHROPIC_API_KEY",
    "INSTAGRAM_ACCESS_TOKEN",
    "INSTAGRAM_APP_SECRET",
    "INSTAGRAM_APP_ID",
    "INSTAGRAM_USER_ID",
    "EMAIL_PASSWORD",
    "IMGBB_API_KEY",
    "CLOUDINARY_API_KEY",
    "CLOUDINARY_API_SECRET",
    "SHOTSTACK_API_KEY",
}

KNOWN_PROJECT_FILES = {
    "main.py", "post.py", "compile.py", "agent.py", "approver.py",
    "preflight.py", "preview.py", "refresh_token.py", "regenerate.py",
    "reset.py", "stats.py", "sync_to_drive.py", "get_token.py",
    "security_sweep.py", "security_check.sh", "sync_watch.sh", "locations.py", "location_map.json", "cron.log",
    "setup_cron.sh", "vps_setup.sh", "requirements.txt",
    ".env", ".env.example", ".gitignore", ".DS_Store",
    "Brand_Prompt.txt", "COMMANDS.md",
    "credentials.json", "agent_memory.json",
    "pending_post.json", "posted_log.json", "token_log.txt",
}

# ── Findings collector ────────────────────────────────────────────────────────

findings = []  # (severity, category, message)

CRITICAL = "CRITICAL"
WARNING  = "WARNING"
OK       = "OK"
INFO     = "INFO"

def add(severity, category, message):
    findings.append((severity, category, message))


# ── Checks ────────────────────────────────────────────────────────────────────

def check_secrets():
    """Scan all source files for values that belong only in .env."""
    print("  Scanning for secret leaks...")
    secrets = {}
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # Only scan for actual credentials, not config values like folder names
            if key in CREDENTIAL_VARS and len(value) >= 10:
                secrets[key] = value

    leaked = False
    for path in PROJECT_DIR.rglob("*"):
        if path.is_dir():
            continue
        if any(d in path.parts for d in SKIP_DIRS):
            continue
        if path.name in SKIP_FILES:
            continue
        if path.suffix not in SCAN_EXTENSIONS:
            continue
        try:
            content = path.read_text(errors="ignore")
        except Exception:
            continue
        for key, value in secrets.items():
            if value in content:
                rel = path.relative_to(PROJECT_DIR)
                add(CRITICAL, "Secret Leak", f"`{rel}` contains the value of `{key}`")
                leaked = True
                break

    if not leaked:
        add(OK, "Secret Leak Scan", "No .env values found in any source file.")


def check_permissions():
    """Sensitive files must have 600 permissions."""
    print("  Checking file permissions...")
    sensitive = ["credentials.json", ".env"]
    for fname in sensitive:
        fpath = PROJECT_DIR / fname
        if not fpath.exists():
            continue
        mode = oct(fpath.stat().st_mode)[-3:]
        if mode != "600":
            add(CRITICAL, "File Permissions", f"`{fname}` has permissions `{mode}` — should be `600`. Run: chmod 600 {fname}")
        else:
            add(OK, "File Permissions", f"`{fname}` is `600`. Good.")


def check_network():
    """Check what's listening on which ports."""
    print("  Scanning open ports...")
    try:
        result = subprocess.run(["ss", "-tlnp"], capture_output=True, text=True, timeout=10)
        lines = [l for l in result.stdout.splitlines() if "LISTEN" in l]
        summary = "\n".join(lines) if lines else "(nothing listening)"

        # ss output columns: State Recv-Q Send-Q Local-Address:Port Peer
        # Flag services whose Local-Address is 0.0.0.0 (not 127.x loopback) and not SSH
        # Ports that are expected on this VPS and are not a concern
        EXPECTED_PORTS = {":22", ":80", ":443"}
        exposed = []
        for l in lines:
            parts = l.split()
            if len(parts) < 4:
                continue
            local = parts[3]  # e.g. "0.0.0.0:5000" or "127.0.0.53%lo:53"
            if local.startswith("0.0.0.0:") and not any(local.endswith(p) for p in EXPECTED_PORTS):
                exposed.append(l)
        if exposed:
            add(WARNING, "Network Exposure",
                f"Services publicly reachable on 0.0.0.0:\n{chr(10).join(exposed)}\n\n"
                "Restrict with ufw or bind to 127.0.0.1 instead.")
        else:
            add(OK, "Network Exposure", "No unexpected services exposed to the internet.")

        add(INFO, "Open Ports (full list)", summary)
    except Exception as e:
        add(INFO, "Network", f"Could not check open ports: {e}")


def check_approver():
    """Approver server check — is it running and what port is exposed?"""
    print("  Checking approver server...")
    try:
        result = subprocess.run(["pgrep", "-fa", "approver.py"], capture_output=True, text=True)
        if result.stdout.strip():
            add(WARNING, "Approver Server",
                "approver.py is running and binds to 0.0.0.0. "
                "Make sure your firewall (ufw) allows only trusted IPs on its port, "
                "or run it behind a reverse proxy. "
                "Token-based auth is in place but the port should not be wide open.")
        else:
            add(OK, "Approver Server", "approver.py is not running.")
    except Exception as e:
        add(INFO, "Approver Server", f"Could not check: {e}")


def check_ssh():
    """Check SSH config for common misconfigurations."""
    print("  Checking SSH config...")
    ssh_config = Path("/etc/ssh/sshd_config")
    if not ssh_config.exists():
        add(INFO, "SSH", "sshd_config not found — skipping.")
        return

    content = ssh_config.read_text()

    # Password auth
    if re.search(r"^PasswordAuthentication\s+yes", content, re.MULTILINE):
        add(WARNING, "SSH", "PasswordAuthentication is enabled. Brute-force risk — disable it and use keys only.")
    else:
        add(OK, "SSH", "Password authentication is disabled (key-only access). Good.")

    # Root login
    root_match = re.search(r"^PermitRootLogin\s+(\S+)", content, re.MULTILINE)
    if root_match:
        value = root_match.group(1)
        if value == "yes":
            add(WARNING, "SSH", "PermitRootLogin is 'yes'. Change to 'prohibit-password' to block password-based root login.")
        else:
            add(OK, "SSH", f"PermitRootLogin is '{value}'. OK.")
    else:
        add(INFO, "SSH", "PermitRootLogin not explicitly set (defaults vary by distro).")


def check_dependencies():
    """Run pip-audit to find known CVEs in installed packages."""
    print("  Scanning dependencies for CVEs...")
    pip_audit = PROJECT_DIR / ".venv/bin/pip-audit"
    if not pip_audit.exists():
        add(INFO, "Dependencies", "pip-audit not installed — skipping CVE scan.")
        return
    try:
        result = subprocess.run(
            [str(pip_audit), "--format", "json", "-r", str(PROJECT_DIR / "requirements.txt")],
            capture_output=True, text=True, timeout=60,
        )
        data = json.loads(result.stdout or "{}")
        deps = data.get("dependencies", data) if isinstance(data, dict) else data
        vulnerable = [d for d in deps if d.get("vulns")]
        if not vulnerable:
            add(OK, "Dependencies", "No known CVEs found in installed packages.")
        else:
            for pkg in vulnerable:
                for v in pkg["vulns"]:
                    add(WARNING, "Dependency CVE",
                        f"`{pkg['name']} {pkg['version']}` — {v['id']}: {v['description'][:120]}")
    except json.JSONDecodeError:
        add(INFO, "Dependencies", "pip-audit returned no JSON output (may be up to date).")
    except Exception as e:
        add(WARNING, "Dependencies", f"pip-audit error: {e}")


def check_unexpected_files():
    """Flag files in the project root that don't belong."""
    print("  Checking for unexpected files...")
    unexpected = []
    for path in PROJECT_DIR.iterdir():
        if path.is_dir():
            continue
        if path.name not in KNOWN_PROJECT_FILES and not path.name.endswith(".pyc"):
            unexpected.append(path.name)
    if unexpected:
        add(WARNING, "Unexpected Files",
            f"Files found in project root that aren't in the known list:\n" +
            "\n".join(f"  • {f}" for f in sorted(unexpected)) +
            "\n\nVerify these are intentional.")
    else:
        add(OK, "Unexpected Files", "No unexpected files found in project root.")


def check_cron():
    """List all cron jobs so you can verify nothing unexpected was added."""
    print("  Checking cron jobs...")
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        jobs = result.stdout.strip() or "(no cron jobs)"
        add(INFO, "Cron Jobs (verify these look right)", jobs)
    except Exception as e:
        add(INFO, "Cron Jobs", f"Could not read crontab: {e}")


# ── Report builder ────────────────────────────────────────────────────────────

COLORS = {
    CRITICAL: "#dc2626",  # red
    WARNING:  "#d97706",  # amber
    OK:       "#16a34a",  # green
    INFO:     "#6b7280",  # gray
}
ICONS = {CRITICAL: "🚨", WARNING: "⚠️", OK: "✅", INFO: "ℹ️"}


def build_html_report(scan_date, counts):
    rows = ""
    for severity, category, message in findings:
        color = COLORS[severity]
        icon  = ICONS[severity]
        # Format code blocks
        msg_html = re.sub(r"`([^`]+)`", r"<code>\1</code>", message)
        msg_html = msg_html.replace("\n", "<br>")
        rows += f"""
        <tr>
          <td style="padding:10px 12px;white-space:nowrap;font-weight:600;color:{color}">{icon} {severity}</td>
          <td style="padding:10px 12px;color:#374151;font-weight:500">{category}</td>
          <td style="padding:10px 12px;color:#4b5563;font-size:13px">{msg_html}</td>
        </tr>"""

    critical_count = counts.get(CRITICAL, 0)
    warning_count  = counts.get(WARNING, 0)
    ok_count       = counts.get(OK, 0)

    status_color = "#dc2626" if critical_count else "#d97706" if warning_count else "#16a34a"
    status_text  = (f"{critical_count} critical issue{'s' if critical_count != 1 else ''}"
                    if critical_count else
                    f"{warning_count} warning{'s' if warning_count != 1 else ''}"
                    if warning_count else
                    "All clear")

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f9fafb;font-family:-apple-system,BlinkMacSystemFont,sans-serif">
<div style="max-width:720px;margin:40px auto;background:white;border-radius:12px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.08)">
  <div style="background:#111;padding:20px 24px;display:flex;justify-content:space-between;align-items:center">
    <p style="color:white;margin:0;font-size:17px;font-weight:600">@your_account — Weekly Security Report</p>
    <p style="color:#9ca3af;margin:0;font-size:13px">{scan_date}</p>
  </div>
  <div style="padding:20px 24px;background:{status_color}10;border-bottom:1px solid {status_color}30">
    <p style="margin:0;font-size:15px;color:{status_color};font-weight:600">{status_text}</p>
    <p style="margin:4px 0 0;font-size:13px;color:#6b7280">{ok_count} passed · {warning_count} warnings · {critical_count} critical</p>
  </div>
  <table style="width:100%;border-collapse:collapse">
    <thead>
      <tr style="background:#f9fafb;border-bottom:1px solid #e5e7eb">
        <th style="padding:10px 12px;text-align:left;font-size:12px;color:#6b7280;text-transform:uppercase">Level</th>
        <th style="padding:10px 12px;text-align:left;font-size:12px;color:#6b7280;text-transform:uppercase">Check</th>
        <th style="padding:10px 12px;text-align:left;font-size:12px;color:#6b7280;text-transform:uppercase">Detail</th>
      </tr>
    </thead>
    <tbody style="divide-y:#e5e7eb">
      {rows}
    </tbody>
  </table>
  <div style="padding:16px 24px;border-top:1px solid #f0f0f0;font-size:12px;color:#9ca3af">
    @your_account security sweep · VPS scan · {scan_date}
  </div>
</div>
</body></html>"""


def send_report(html, scan_date, counts):
    sender   = os.getenv("EMAIL_SENDER")
    password = os.getenv("EMAIL_PASSWORD")
    receiver = os.getenv("EMAIL_RECEIVER")
    if not all([sender, password, receiver]):
        print("  Email skipped — EMAIL_SENDER/PASSWORD/RECEIVER not set.")
        return

    critical = counts.get(CRITICAL, 0)
    warning  = counts.get(WARNING, 0)
    subject  = (f"🚨 @your_account Security — {critical} critical issue{'s' if critical != 1 else ''}"
                if critical else
                f"⚠️ @your_account Security — {warning} warning{'s' if warning != 1 else ''}"
                if warning else
                f"✅ @your_account Security — All clear ({scan_date})")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = sender
    msg["To"]      = receiver
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(sender, password)
        smtp.send_message(msg)
    print(f"  Report emailed to {receiver}.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    scan_date = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'='*50}")
    print(f"  @your_account Security Sweep — {scan_date}")
    print(f"{'='*50}\n")

    check_secrets()
    check_permissions()
    check_network()
    check_approver()
    check_ssh()
    check_dependencies()
    check_unexpected_files()
    check_cron()

    counts = {}
    for severity, _, _ in findings:
        counts[severity] = counts.get(severity, 0) + 1

    print(f"\n  Results: {counts.get(CRITICAL,0)} critical · "
          f"{counts.get(WARNING,0)} warnings · {counts.get(OK,0)} passed\n")

    html = build_html_report(scan_date, counts)
    send_report(html, scan_date, counts)

    print("Done.\n")


if __name__ == "__main__":
    main()
