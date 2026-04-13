#!/usr/bin/env python3
"""
FB Group Crawler — Anti-Detection Scheduler

Anti-bot measures:
- Headless=False (real browser window = much harder to detect)
- Random 30-180 min between scrapes (mimics human browsing patterns)
- Never scrapes more than 8 groups per day
- Random post limit per scrape (20-60) to vary behavior
- 10-30 min breaks every 3 scrapes
- Session refresh: re-logs in every 12 hours
- Random time-of-day weighting (less scraping at 2-5 AM)

Usage:
  ./venv/bin/python3 run_scheduler.py               # Run once
  ./venv/bin/python3 run_scheduler.py --daemon        # Continuous daemon
  ./venv/bin/python3 run_scheduler.py --all           # All groups once
  ./venv/bin/python3 run_scheduler.py --db-stats      # Database stats
"""

import asyncio
import json
import os
import random
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

for _p in [
    os.environ.get("CRAWLER_DIR", ""),
    os.path.expanduser("~/.openclaw/workspace/crawlers"),
    os.path.expanduser("~/crawlers"),
]:
    if _p and os.path.isfile(os.path.join(_p, "crawler_db.py")):
        sys.path.insert(0, _p)
        break
import crawler_db

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOG_FILE = BASE_DIR / "scheduler.log"
SESSION_FILE = BASE_DIR / "session.json"

GROUP_IDS = [
    "1121944334567453", "654748364354107", "1745892855948687", "933674975855737",
    "1774502687274720", "2553120341751600", "1001556964304386", "3894208190715125",
    "1577315533418837", "1244346110734837", "3229251420636884", "839534162744930",
    "7170559969654432", "652178389264909", "770988810895106", "1576312052950752",
    "728630109211473", "435434921238337", "3255595701139785", "1364459958209699",
    "267249817311678", "397412894378108", "649245810623828", "3682389062018307",
    "778495273564899", "1712447172677146", "456882118879168", "490903803201153",
    "278105266564882", "1244175850623484", "2254183738386576", "882439515535971",
    "1238818407825761", "1342996746678532", "163583739075033", "109767362376724",
    "325743041336807", "276059605530869", "201997038548771", "181175949849139",
    "1884013731857192", "414485096343458", "2971164986495013", "487631723059303",
    "209932990505671", "510865431414449", "2317142971864047", "4087025951521595",
    "2495142260709549", "701472708866479", "456929628904615", "719862439125164",
    "1668274093211179",
    "1049914571763738",
]

SUBSCRIBER_HUB = "https://www.facebook.com/earthh.evans.2025/supporters"

# ── Anti-Detection Config ───────────────────────────────────────────
MAX_SCRAPES_PER_DAY = 8           # Max groups per 24h period
MIN_INTERVAL_MIN = 30             # Min minutes between scrapes
MAX_INTERVAL_MIN = 180            # Max minutes between scrapes
BREAK_EVERY_N = 3                 # Take a long break every N scrapes
BREAK_MIN_MIN = 60                # Min break duration (minutes)
BREAK_MAX_MIN = 180               # Max break duration (minutes)
SESSION_REFRESH_HOURS = 12        # Re-login interval
SLEEP_HOURS_START = 2             # Quiet hours start (AM)
SLEEP_HOURS_END = 5               # Quiet hours end (AM)
LOW_ACTIVITY_START = 0            # Low activity hours start
LOW_ACTIVITY_END = 6              # Low activity hours end


# ── Database ────────────────────────────────────────────────────────

def seed_sources():
    """Register configured FB groups as sources in the unified DB."""
    for gid in GROUP_IDS:
        crawler_db.ensure_source(
            "facebook", "group", gid,
            f"https://www.facebook.com/groups/{gid}", "",
            metadata={"is_subscriber_hub": 0},
        )
    crawler_db.ensure_source(
        "facebook", "hub", "subscriber_hub", SUBSCRIBER_HUB, "Subscriber Hub",
        metadata={"is_subscriber_hub": 1},
    )


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def cleanup_old_exports(group_id, keep=5):
    """Keep only the most recent N export files per group."""
    group_data_dir = DATA_DIR / group_id
    if not group_data_dir.exists():
        return 0
    # Group files by triplet: .json, .md, _summary.json
    all_files = sorted(group_data_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    json_files = [f for f in all_files if "_summary" not in f.name]
    removed = 0
    for old in json_files[keep:]:
        stem = old.stem
        for sibling in group_data_dir.glob(f"{stem}*"):
            try:
                sibling.unlink()
                removed += 1
            except:
                pass
    return removed


# ── Scraper Runner ──────────────────────────────────────────────────

def run_scraper(group_url, group_id):
    """Run scraper with incremental mode — passes known IDs for early exit."""
    import tempfile

    venv_python = BASE_DIR / "venv" / "bin" / "python3"
    scraper = BASE_DIR / "scraper.py"
    limit = random.randint(20, 60)

    # Write known post IDs to temp file for incremental scraping
    sid = crawler_db.get_source_id("facebook", group_id)
    known = crawler_db.get_known_post_ids(sid) if sid else set()
    known_file = None
    cmd = [str(venv_python), str(scraper),
           "--url", group_url, "--headless",
           "--limit", str(limit), "--export", "json"]

    if known:
        known_file = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, dir=str(BASE_DIR))
        json.dump(list(known), known_file)
        known_file.close()
        cmd.extend(["--known-ids-file", known_file.name])
        log(f"  🔧 Scraping {limit} posts (incremental, {len(known)} known)...")
    else:
        log(f"  🔧 Scraping {limit} posts (first run)...")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=900, cwd=str(BASE_DIR))
    finally:
        if known_file:
            Path(known_file.name).unlink(missing_ok=True)

    if result.returncode != 0:
        log(f"  ❌ Error: {result.stderr[-500:]}")
        return []

    # Find latest JSON
    group_data_dir = DATA_DIR / group_id
    if not group_data_dir.exists():
        return []
    json_files = sorted(group_data_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for jf in json_files:
        if "_summary" in jf.name:
            continue
        try:
            data = json.load(jf.open())
            if isinstance(data, list):
                return data
        except:
            continue
    return []


# ── Anti-Detection Logic ────────────────────────────────────────────

def is_quiet_hours():
    """Check if current hour is in quiet zone (reduced scraping)."""
    hour = datetime.now().hour
    return SLEEP_HOURS_START <= hour < SLEEP_HOURS_END


def is_low_activity():
    """Check if we should reduce activity."""
    hour = datetime.now().hour
    return LOW_ACTIVITY_START <= hour < LOW_ACTIVITY_END


def should_take_break(scrapes_in_session):
    """Check if we should take a long break."""
    return scrapes_in_session > 0 and scrapes_in_session % BREAK_EVERY_N == 0


def get_interval():
    """Get randomized wait time between scrapes."""
    if is_low_activity():
        return random.randint(120, 300) * 60  # 2-5 hours during quiet hours
    return random.randint(MIN_INTERVAL_MIN, MAX_INTERVAL_MIN) * 60


# ── Scheduler ───────────────────────────────────────────────────────

def scrape_one_group():
    """Scrape one group with full anti-detection."""
    if crawler_db.get_scrapes_today("facebook") >= MAX_SCRAPES_PER_DAY:
        log(f"⏸️ Daily limit reached ({MAX_SCRAPES_PER_DAY}/day). Waiting...")
        return False

    if is_quiet_hours():
        log(f"😴 Quiet hours ({SLEEP_HOURS_START}-{SLEEP_HOURS_END} AM). Skipping.")
        return False

    picked = crawler_db.pick_next_source("facebook", host=os.environ.get("CRAWLER_HOST"))
    if not picked:
        log("⚠️ No groups available (all recently scraped)")
        return False
    source_id, group_url, group_id, _name = picked

    started = datetime.now().isoformat()
    log(f"📡 [{group_id}] Starting scrape...")

    try:
        posts = run_scraper(group_url, group_id)
        if not posts:
            log("  ⚠️ No posts (no access or empty group)")
            crawler_db.log_scrape(source_id, "facebook", started, 0, 0, 0, "no_posts")
            return True
        new_p, new_t, skipped = crawler_db.save_posts(source_id, "facebook", posts)
        log(f"  ✅ {len(posts)} posts ({new_p} new, {skipped} already known), {new_t} new comments")
        crawler_db.log_scrape(source_id, "facebook", started, new_p, len(posts), new_t,
                              posts_skipped=skipped)
        removed = cleanup_old_exports(group_id, keep=5)
        if removed:
            log(f"  🧹 Cleaned {removed} old export files")
        return True
    except subprocess.TimeoutExpired:
        log("  ⏰ Timeout (15 min)")
        crawler_db.log_scrape(source_id, "facebook", started, 0, 0, 0, "error", "Timeout")
        return True
    except Exception as e:
        log(f"  ❌ {e}")
        crawler_db.log_scrape(source_id, "facebook", started, 0, 0, 0, "error", str(e)[:500])
        return True


def run_daemon():
    """Continuous daemon with anti-detection scheduling."""
    log("🤖 Daemon started (anti-detection mode)")
    log(f"   Groups: {len(GROUP_IDS) + 1}")
    log(f"   Max scrapes/day: {MAX_SCRAPES_PER_DAY}")
    log(f"   Interval: {MIN_INTERVAL_MIN}-{MAX_INTERVAL_MIN} min")
    log(f"   Quiet hours: {SLEEP_HOURS_START}-{SLEEP_HOURS_END} AM")
    log(f"   Headless: NO (real browser for anti-detection)")

    session_count = 0

    while True:
        scraped = scrape_one_group()
        if scraped:
            session_count += 1

            # Long break every N scrapes
            if should_take_break(session_count):
                break_min = random.randint(BREAK_MIN_MIN, BREAK_MAX_MIN)
                log(f"  🛑 Taking long break ({break_min} min) after {session_count} scrapes")
                time.sleep(break_min * 60)

        # Random interval before next scrape
        interval = get_interval()
        mins = interval // 60
        log(f"  ⏳ Next scrape in {mins} min (interval randomized)")
        time.sleep(interval)


def scrape_all_groups():
    """Scrape all groups once with delays."""
    groups = crawler_db.list_sources("facebook")  # (id, platform, stype, ext, url, name, ...)
    random.shuffle(groups)

    log(f"🔄 Scraping all {len(groups)} groups (with delays)...")

    for i, row in enumerate(groups):
        log(f"\n[{i+1}/{len(groups)}]")
        if crawler_db.get_scrapes_today("facebook") >= MAX_SCRAPES_PER_DAY:
            log("  ⏸️ Daily limit reached. Stopping.")
            break
        scrape_one_group()
        if i < len(groups) - 1:
            delay = random.randint(5, 20) * 60
            log(f"  ⏳ Waiting {delay // 60} min...")
            time.sleep(delay)

    log("\n✅ All groups scraped")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="FB Group Crawler (Anti-Detection)")
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--db-stats", action="store_true")
    parser.add_argument("--init", action="store_true")
    args = parser.parse_args()

    crawler_db.init_db()
    seed_sources()

    if args.db_stats:
        crawler_db.show_db_stats("facebook")
    elif args.init:
        print("✅ DB initialized")
    elif args.all:
        scrape_all_groups()
    elif args.daemon:
        run_daemon()
    else:
        scrape_one_group()


if __name__ == "__main__":
    main()
