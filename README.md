# FB Group Scraper

Anti-detection Facebook group scraper using Playwright + stealth.

## Setup

```bash
cd ~/.openclaw/workspace/fb-group-scraper
source venv/bin/activate
```

## First Run — Login

```bash
python scraper.py --login
```

Opens a real Chrome browser → you log in manually → session cookies are saved for future runs.

## Scrape a Group

```bash
# Basic: 100 posts
python scraper.py --url "https://www.facebook.com/groups/GROUP_SLUG"

# With comments, 50 posts, JSON export only
python scraper.py --url "https://www.facebook.com/groups/GROUP_SLUG" --comments --limit 50 --export json

# Both JSON + Markdown
python scraper.py --url "https://www.facebook.com/groups/GROUP_SLUG" --comments --limit 200 --export both
```

## Anti-Detection Measures

- **Playwright Stealth** — removes automation fingerprints
- **Real browser** (headless=False) — much harder to detect than headless
- **Persistent session** — uses your real logged-in cookies
- **Randomized delays** — 1.5-4s between scrolls, 0.3-1s between extractions
- **Human mouse movement** — random mouse jitter between actions
- **Variable scroll distance** — not mechanical pixel-perfect scrolling
- **NAT viewport + user-agent** — mimics Windows Chrome

## Output

- `data/` directory — JSON and/or Markdown files per scrape
- Each post includes: author, text, timestamp, reactions, comments (if enabled), post ID, scrape timestamp

## Tips

- Don't scrape more than 200-300 posts per session to stay safe
- Wait 5-10 min between scraping sessions
- If you get flagged, delete `session.json` and re-login after a cooldown
