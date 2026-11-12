# ig-auto: Instagram Autoposter with an AI Content Strategist

An Instagram posting system for a faceless lifestyle account. Photos and clips go into a
Google Drive folder, and the system writes the caption, builds Reels, asks for approval by
email, posts on a schedule, and learns from what performs.

"Nova" is the AI strategist inside it. It researches trends in the niche, scans and tags the
clips in Drive, plans the week, and writes a brief for the next Reel.

## What it does

| Step | File |
|---|---|
| Pick the next unposted photo or video from Drive, write a caption with Claude | `main.py` |
| Build a Reel from the best clips, with music and text | `compile.py` |
| Email a preview with one-click Approve / Reject | `approver.py`, `notify.py`, `preview.py` |
| Post to Instagram through the Graph API | `post.py` |
| Research trends, scan clips, write the weekly strategy | `agent.py` |
| Track likes, comments and reach, and feed results back | `engagement_tracker.py`, `stats.py` |
| Turn raw uploads into postable files | `process_raw.py` |
| Refresh the Instagram token before it expires | `refresh_token.py` |
| Run it all on a server on a schedule | `setup_cron.sh`, `vps_setup.sh`, `sync_watch.sh` |
| Scan for leaked secrets on every sync and once a week | `security_check.sh`, `security_sweep.py` |

Place details for captions live in `location_map.json`. The brand voice lives in
`Brand_Prompt.txt` (an example template here).

## Tools and tech

Python, Claude API, Instagram Graph API, Google Drive API, Cloudinary, Twelve Labs (video
understanding), cron on a Linux server.

## Setup

```bash
git clone https://github.com/letammythings/instagram-autoposter.git
cd instagram-autoposter
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # add your keys
```

You also need a Google Cloud `credentials.json` for Drive access (not included).

See `COMMANDS.md` for every command.
