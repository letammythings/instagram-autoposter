# @your_account Autoposter — Command Cheat Sheet

## Post a single photo or video from Drive
```bash
python3 main.py                        # picks next unposted file from whole vault
python3 main.py --folder "ba na"       # picks from a specific subfolder
python3 post.py                        # posts whatever is queued
```

## Build a compiled Reel (Nova)
```bash
python3 compile.py              # picks best clips from whole vault
python3 compile.py --nova       # uses Nova's strategy to pick clips
python3 compile.py --folder "ba na"  # only use clips from a subfolder
python3 compile.py --nova --folder "ba na"  # Nova strategy + specific subfolder
python3 post.py                 # post the compiled reel
```

## Nova AI strategy
```bash
python3 agent.py              # full run: research + trends + scan + strategy + brief
python3 agent.py --research   # research trending content in your niche
python3 agent.py --trends     # scrape web for real-time trending Instagram style data
python3 agent.py --scan       # scan & classify clips in your Drive vault
python3 agent.py --strategy   # generate weekly content strategy
python3 agent.py --brief      # create a compile brief for next Reel
python3 agent.py --analyze-trends  # extract style patterns from your saved trending captions
```

## Feed trending captions (teach Nova your taste)
When you see a caption on IG you love, save it so Nova can learn from it:
```bash
python3 add_trend.py "caption text here"
python3 add_trend.py "caption text here" --notes "love the price contrast hook"
python3 add_trend.py "caption text here" --source "faceless food account, 80k followers" --notes "great hook"

# After saving a few, run this to extract patterns into Nova:
python3 agent.py --analyze-trends
```
The more you add, the better Nova understands what style works for you.

## Preview & manage pending posts
```bash
python3 preview.py      # see what's queued before posting
python3 regenerate.py   # regenerate the caption for the queued post
python3 reset.py        # clear the queue and skip the current file
```

## Check stats
```bash
python3 stats.py        # see posting history and counts
```

## Process raw videos (Twelve Labs AI)
```bash
python3 process_raw.py                    # process all subfolders in the vault
python3 process_raw.py --folder "hoi an"  # process one subfolder only
python3 process_raw.py --dry-run          # list unprocessed videos without doing anything
```
> Drop long raw videos (> 30s) into any existing subfolder (Ba na, hoi an, vn food, etc.).
> Twelve Labs finds the best moments → clips are cut and uploaded back to the same subfolder.
> Requires `TWELVELABS_API_KEY` in `.env.master`.

## Token & auth
```bash
python3 get_token.py    # get a new Instagram access token
python3 refresh_token.py  # refresh existing token before it expires
```

## VPS (run these over SSH)
```bash
ssh user@YOUR_VPS_IP          # connect to VPS
git pull                          # pull latest code changes from GitHub
python3 approver.py               # start the email approval server
tail -f logs/post.log             # watch live posting logs
./setup_cron.sh                   # re-apply the automated schedule
```

## Update code on VPS (after making changes on Mac)
```bash
# On your Mac:
git add .
git commit -m "your message"
git push

# On the VPS (over SSH):
# One-time cleanup if pending_post.json / posted_log.json were still tracked:
git rm --cached pending_post.json posted_log.json

# Then pull normally:
git pull
```

## Automated schedule (runs itself)
| What | When |
|------|------|
| Daily photo post | Mon, Wed–Sat at 6am Vietnam time |
| Tuesday photo post | Every Tuesday at 6pm Vietnam time |
| Research + strategy refresh | Every Tuesday at 6am Vietnam time |
| Weekly compiled Reel | Every Sunday at 6am Vietnam time |
| Raw video processor | Every day at 2am Vietnam time |
