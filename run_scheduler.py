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
import random
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DB_FILE = BASE_DIR / "fb_groups.db"
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
]

SUBSCRIBER_HUB = "https://www.facebook.com/earthh.evans.2025/supporters"

# ── Anti-Detection Config ───────────────────────────────────────────
MAX_SCRAPES_PER_DAY = 24           # Max groups per 24h period
MIN_INTERVAL_MIN = 15             # Min minutes between scrapes
MAX_INTERVAL_MIN = 90            # Max minutes between scrapes
BREAK_EVERY_N = 3                 # Take a long break every N scrapes
BREAK_MIN_MIN = 20                # Min break duration (minutes)
BREAK_MAX_MIN = 60               # Max break duration (minutes)
SESSION_REFRESH_HOURS = 12        # Re-login interval
SLEEP_HOURS_START = 2             # Quiet hours start (AM)
SLEEP_HOURS_END = 5               # Quiet hours end (AM)
LOW_ACTIVITY_START = 0            # Low activity hours start
LOW_ACTIVITY_END = 6              # Low activity hours end


# ── Database ────────────────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect(str(DB_FILE))
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS groups (
            group_id TEXT PRIMARY KEY,
            group_url TEXT,
            group_name TEXT DEFAULT '',
            last_scraped TEXT,
            total_posts INTEGER DEFAULT 0,
            total_comments INTEGER DEFAULT 0,
            is_subscriber_hub INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS posts (
            post_id TEXT PRIMARY KEY,
            group_id TEXT NOT NULL,
            post_url TEXT,
            author TEXT DEFAULT '',
            text TEXT DEFAULT '',
            timestamp TEXT DEFAULT '',
            reactions INTEGER DEFAULT 0,
            image_urls TEXT DEFAULT '[]',
            video_url TEXT DEFAULT '',
            image_content TEXT DEFAULT '[]',
            scraped_at TEXT,
            FOREIGN KEY (group_id) REFERENCES groups(group_id)
        );
        CREATE INDEX IF NOT EXISTS idx_posts_group ON posts(group_id);
        CREATE INDEX IF NOT EXISTS idx_posts_date ON posts(scraped_at);

        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            author TEXT DEFAULT '',
            text TEXT DEFAULT '',
            timestamp TEXT DEFAULT '',
            timestamp_raw TEXT DEFAULT '',
            scraped_at TEXT,
            FOREIGN KEY (post_id) REFERENCES posts(post_id)
        );
        CREATE INDEX IF NOT EXISTS idx_comments_post ON comments(post_id);
        CREATE INDEX IF NOT EXISTS idx_comments_group ON comments(group_id);

        CREATE TABLE IF NOT EXISTS scrape_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT,
            started_at TEXT,
            finished_at TEXT,
            posts_new INTEGER DEFAULT 0,
            posts_total INTEGER DEFAULT 0,
            comments_new INTEGER DEFAULT 0,
            status TEXT DEFAULT 'success',
            error TEXT DEFAULT ''
        );
    """)
    for gid in GROUP_IDS:
        c.execute("INSERT OR IGNORE INTO groups (group_id, group_url) VALUES (?, ?)",
                  (gid, f"https://www.facebook.com/groups/{gid}"))
    c.execute("INSERT OR IGNORE INTO groups (group_id, group_url, group_name, is_subscriber_hub) VALUES (?, ?, ?, 1)",
              ("subscriber_hub", SUBSCRIBER_HUB, "Subscriber Hub"))
    conn.commit()
    conn.close()
    log(f"Database initialized: {DB_FILE}")


def get_known_post_ids(group_id):
    conn = sqlite3.connect(str(DB_FILE))
    c = conn.cursor()
    c.execute("SELECT post_id FROM posts WHERE group_id = ?", (group_id,))
    ids = {row[0] for row in c.fetchall()}
    conn.close()
    return ids


def save_posts(group_id, posts):
    known = get_known_post_ids(group_id)
    new_posts = 0
    new_comments = 0
    conn = sqlite3.connect(str(DB_FILE))
    c = conn.cursor()
    for post in posts:
        pid = post.get("id", "")
        if not pid or pid in known:
            continue
        new_posts += 1
        c.execute("""
            INSERT OR REPLACE INTO posts
            (post_id, group_id, post_url, author, text, timestamp, reactions,
             image_urls, video_url, image_content, scraped_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (pid, group_id,
              post.get("url", f"https://www.facebook.com/groups/{group_id}/posts/{pid}"),
              post.get("author", ""), post.get("text", ""), post.get("timestamp", ""),
              post.get("reactions", 0), json.dumps(post.get("image_urls", [])),
              post.get("video_url", ""), json.dumps(post.get("image_content", [])),
              post.get("scraped_at", "")))
        for comment in post.get("comments", []):
            new_comments += 1
            c.execute("INSERT INTO comments (post_id, group_id, author, text, timestamp, timestamp_raw, scraped_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                      (pid, group_id, comment.get("author", ""), comment.get("text", ""),
                       comment.get("timestamp", ""), comment.get("timestamp_raw", ""),
                       post.get("scraped_at", "")))
    now = datetime.now().isoformat()
    c.execute("""UPDATE groups SET last_scraped = ?,
                total_posts = (SELECT COUNT(*) FROM posts WHERE group_id = ?),
                total_comments = (SELECT COUNT(*) FROM comments WHERE group_id = ?)
                WHERE group_id = ?""", (now, group_id, group_id, group_id))
    conn.commit()
    conn.close()
    return new_posts, new_comments


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def log_scrape(group_id, started, posts_new, posts_total, comments_new, status="success", error=""):
    conn = sqlite3.connect(str(DB_FILE))
    c = conn.cursor()
    c.execute("""INSERT INTO scrape_log (group_id, started_at, finished_at, posts_new, posts_total, comments_new, status, error)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
              (group_id, started, datetime.now().isoformat(), posts_new, posts_total, comments_new, status, error))
    conn.commit()
    conn.close()


def show_db_stats():
    conn = sqlite3.connect(str(DB_FILE))
    c = conn.cursor()
    print(f"\n📊 Database Stats")
    print(f"   Groups:  {c.execute('SELECT COUNT(*) FROM groups').fetchone()[0]}")
    print(f"   Posts:   {c.execute('SELECT COUNT(*) FROM posts').fetchone()[0]}")
    print(f"   Comments: {c.execute('SELECT COUNT(*) FROM comments').fetchone()[0]}")
    ok = c.execute("SELECT COUNT(*) FROM scrape_log WHERE status='success'").fetchone()[0]
    err = c.execute("SELECT COUNT(*) FROM scrape_log WHERE status='error'").fetchone()[0]
    print(f"   Scrapes: {ok} success, {err} errors")
    print()
    c.execute("SELECT group_id, total_posts, total_comments, last_scraped FROM groups ORDER BY total_posts DESC LIMIT 10")
    print("   Top groups:")
    for row in c.fetchall():
        print(f"     {row[0]}: {row[1]} posts, {row[2]} comments (last: {row[3] or 'never'})")
    c.execute("SELECT group_id FROM groups WHERE last_scraped IS NULL")
    never = [r[0] for r in c.fetchall()]
    if never:
        print(f"\n   Never scraped ({len(never)}): {', '.join(never[:5])}...")
    conn.close()


# ── Scraper Runner ──────────────────────────────────────────────────

def run_scraper(group_url, group_id):
    """Run scraper with VISIBLE browser (anti-detection priority)."""
    venv_python = BASE_DIR / "venv" / "bin" / "python3"
    scraper = BASE_DIR / "scraper.py"
    limit = random.randint(40, 100)  # Random limit per scrape

    log(f"  🔧 Scraping {limit} posts (randomized limit)...")

    result = subprocess.run(
        [str(venv_python), str(scraper),
         "--url", group_url,
         "--headless",
         "--limit", str(limit), "--deep", str(min(limit, 50)),
         "--export", "json"],
        capture_output=True, text=True,
        timeout=900,  # 15 min max
        cwd=str(BASE_DIR),
    )

    if result.returncode != 0:
        log(f"  ❌ Error: {result.stderr[-500:]}")
        return []

    # Find latest JSON
    group_data_dir = DATA_DIR / group_id
    if not group_data_dir.exists():
        return []
    json_files = sorted(group_data_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for jf in json_files:
        try:
            data = json.load(jf.open())
            if isinstance(data, list):
                return data
        except:
            continue
    return []


# ── Anti-Detection Logic ────────────────────────────────────────────

def get_scrapes_today():
    """Count scrapes in the last 24 hours."""
    conn = sqlite3.connect(str(DB_FILE))
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM scrape_log WHERE started_at > datetime('now', '-24 hours')")
    count = c.fetchone()[0]
    conn.close()
    return count


def is_quiet_hours():
    """Check if current hour is in quiet zone (reduced scraping)."""
    hour = datetime.now().hour
    return SLEEP_HOURS_START <= hour < SLEEP_HOURS_END


def is_low_activity():
    """Check if we should reduce activity."""
    hour = datetime.now().hour
    return LOW_ACTIVITY_START <= hour < LOW_ACTIVITY_END


def pick_next_group():
    """Pick next group — prioritize unscraped, avoid recently scraped."""
    conn = sqlite3.connect(str(DB_FILE))
    c = conn.cursor()

    # Get groups scraped in last 6 hours (avoid these)
    c.execute("SELECT group_id FROM scrape_log WHERE started_at > datetime('now', '-6 hours')")
    recent = {r[0] for r in c.fetchall()}

    # Prefer: never scraped > oldest scrape
    if recent:
        c.execute("""
            SELECT group_id, group_url FROM groups
            WHERE group_id NOT IN ({})
            ORDER BY COALESCE(last_scraped, '1970-01-01') ASC
            LIMIT 5
        """.format(','.join(['?'] * len(recent))), list(recent))
    else:
        c.execute("""
            SELECT group_id, group_url FROM groups
            ORDER BY COALESCE(last_scraped, '1970-01-01') ASC
            LIMIT 5
        """)
    candidates = c.fetchall()

    if not candidates:
        return None, None
    return random.choice(candidates)


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
    # Daily limit check
    if get_scrapes_today() >= MAX_SCRAPES_PER_DAY:
        log(f"⏸️ Daily limit reached ({MAX_SCRAPES_PER_DAY}/day). Waiting...")
        return False

    # Quiet hours check
    if is_quiet_hours():
        log(f"😴 Quiet hours ({SLEEP_HOURS_START}-{SLEEP_HOURS_END} AM). Skipping.")
        return False

    group_id, group_url = pick_next_group()
    if not group_id:
        log("⚠️ No groups available (all recently scraped)")
        return False

    started = datetime.now().isoformat()
    log(f"📡 [{group_id}] Starting scrape...")

    try:
        posts = run_scraper(group_url, group_id)
        if not posts:
            log(f"  ⚠️ No posts (no access or empty group)")
            log_scrape(group_id, started, 0, 0, 0, "no_posts")
            return True

        new_p, new_c = save_posts(group_id, posts)
        log(f"  ✅ {len(posts)} posts ({new_p} new), {new_c} comments")
        log_scrape(group_id, started, new_p, len(posts), new_c)
        return True

    except subprocess.TimeoutExpired:
        log(f"  ⏰ Timeout (15 min)")
        log_scrape(group_id, started, 0, 0, 0, "error", "Timeout")
        return True
    except Exception as e:
        log(f"  ❌ {e}")
        log_scrape(group_id, started, 0, 0, 0, "error", str(e)[:500])
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
    conn = sqlite3.connect(str(DB_FILE))
    c = conn.cursor()
    c.execute("SELECT group_id, group_url FROM groups ORDER BY RANDOM()")
    groups = c.fetchall()
    conn.close()

    log(f"🔄 Scraping all {len(groups)} groups (with delays)...")

    for i, (gid, url) in enumerate(groups):
        log(f"\n[{i+1}/{len(groups)}]")

        # Skip if already scraped today
        if get_scrapes_today() >= MAX_SCRAPES_PER_DAY:
            log(f"  ⏸️ Daily limit reached. Stopping.")
            break

        scrape_one_group()

        # Delay between groups (5-20 min)
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

    init_db()

    if args.db_stats:
        show_db_stats()
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
