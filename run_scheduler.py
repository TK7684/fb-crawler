#!/usr/bin/env python3
"""
FB Group Crawler — Cron Scheduler + Database

Randomizes scrape times across 55 groups to avoid bot detection.
Uses SQLite for deduplication and persistent storage.

Usage:
  source venv/bin/activate
  python run_scheduler.py                  # Run once (scrape random group)
  python run_scheduler.py --daemon         # Run as continuous daemon
  python run_scheduler.py --all            # Scrape all groups once
  python run_scheduler.py --db-stats       # Show database stats
"""

import asyncio
import json
import os
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

    # Register groups
    for gid in GROUP_IDS:
        url = f"https://www.facebook.com/groups/{gid}"
        c.execute("INSERT OR IGNORE INTO groups (group_id, group_url) VALUES (?, ?)", (gid, url))

    # Subscriber hub
    c.execute("INSERT OR IGNORE INTO groups (group_id, group_url, group_name, is_subscriber_hub) VALUES (?, ?, ?, 1)",
              ("subscriber_hub", SUBSCRIBER_HUB, "Subscriber Hub"))

    conn.commit()
    conn.close()
    log(f"Database initialized: {DB_FILE}")


def get_known_post_ids(group_id):
    """Get all post IDs we already have for a group (dedup)."""
    conn = sqlite3.connect(str(DB_FILE))
    c = conn.cursor()
    c.execute("SELECT post_id FROM posts WHERE group_id = ?", (group_id,))
    ids = {row[0] for row in c.fetchall()}
    conn.close()
    return ids


def save_posts(group_id, posts):
    """Save new posts + comments to database, return counts."""
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
        """, (
            pid,
            group_id,
            post.get("url", f"https://www.facebook.com/groups/{group_id}/posts/{pid}"),
            post.get("author", ""),
            post.get("text", ""),
            post.get("timestamp", ""),
            post.get("reactions", 0),
            json.dumps(post.get("image_urls", [])),
            post.get("video_url", ""),
            json.dumps(post.get("image_content", [])),
            post.get("scraped_at", ""),
        ))

        # Save comments
        for comment in post.get("comments", []):
            new_comments += 1
            c.execute("""
                INSERT INTO comments (post_id, group_id, author, text, scraped_at)
                VALUES (?, ?, ?, ?, ?)
            """, (
                pid,
                group_id,
                comment.get("author", ""),
                comment.get("text", ""),
                post.get("scraped_at", ""),
            ))

    # Update group stats
    now = datetime.now().isoformat()
    c.execute("""
        UPDATE groups SET
            last_scraped = ?,
            total_posts = (SELECT COUNT(*) FROM posts WHERE group_id = ?),
            total_comments = (SELECT COUNT(*) FROM comments WHERE group_id = ?)
        WHERE group_id = ?
    """, (now, group_id, group_id, group_id))

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
    c.execute("""
        INSERT INTO scrape_log (group_id, started_at, finished_at, posts_new, posts_total, comments_new, status, error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (group_id, started, datetime.now().isoformat(), posts_new, posts_total, comments_new, status, error))
    conn.commit()
    conn.close()


def show_db_stats():
    conn = sqlite3.connect(str(DB_FILE))
    c = conn.cursor()

    c.execute("SELECT COUNT(*) FROM groups")
    total_groups = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM posts")
    total_posts = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM comments")
    total_comments = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM scrape_log WHERE status='success'")
    total_scrapes = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM scrape_log WHERE status='error'")
    total_errors = c.fetchone()[0]

    print(f"\n📊 Database Stats")
    print(f"   Groups:       {total_groups}")
    print(f"   Posts:        {total_posts}")
    print(f"   Comments:     {total_comments}")
    print(f"   Scrape runs:  {total_scrapes} success, {total_errors} errors")
    print()

    # Top groups by posts
    c.execute("""
        SELECT g.group_id, g.group_name, g.total_posts, g.total_comments, g.last_scraped
        FROM groups g ORDER BY g.total_posts DESC LIMIT 10
    """)
    print("   Top groups by posts:")
    for row in c.fetchall():
        gid, name, posts, comments, last = row
        print(f"     {name or gid}: {posts} posts, {comments} comments (last: {last or 'never'})")

    # Never scraped
    c.execute("SELECT group_id, group_url FROM groups WHERE last_scraped IS NULL")
    never = c.fetchall()
    if never:
        print(f"\n   Never scraped ({len(never)}):")
        for row in never[:10]:
            print(f"     {row[0]}")

    conn.close()


# ── Scraper Runner ──────────────────────────────────────────────────

def run_scraper(group_url, group_id, limit=50):
    """Run the scraper for a single group, return parsed JSON."""
    venv_python = BASE_DIR / "venv" / "bin" / "python3"
    scraper = BASE_DIR / "scraper.py"

    result = subprocess.run(
        [str(venv_python), str(scraper),
         "--url", group_url,
         "--comments",
         "--headless",
         "--limit", str(limit),
         "--export", "json"],
        capture_output=True, text=True,
        timeout=600,  # 10 min max per group
        cwd=str(BASE_DIR),
    )

    if result.returncode != 0:
        log(f"  ❌ Scraper error: {result.stderr[-300:]}")
        return []

    # Find the latest JSON output
    if group_id != "subscriber_hub":
        group_data_dir = DATA_DIR / group_id
    else:
        group_data_dir = DATA_DIR / "subscriber_hub"

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


# ── Scheduler ───────────────────────────────────────────────────────

def get_next_scrape_delay():
    """Random delay between 15-90 minutes to look human."""
    return random.randint(15 * 60, 90 * 60)


def pick_next_group():
    """Pick a group to scrape next — prefer least recently scraped."""
    conn = sqlite3.connect(str(DB_FILE))
    c = conn.cursor()

    # 50% chance: pick a group never scraped or oldest scrape
    # 50% chance: pick random
    if random.random() < 0.5:
        c.execute("""
            SELECT group_id, group_url FROM groups
            WHERE last_scraped IS NULL OR last_scraped < datetime('now', '-1 day')
            ORDER BY COALESCE(last_scraped, '1970-01-01') ASC
            LIMIT 5
        """)
        candidates = c.fetchall()
    else:
        c.execute("SELECT group_id, group_url FROM groups")
        candidates = c.fetchall()

    conn.close()

    if not candidates:
        return None, None

    return random.choice(candidates)


def scrape_one_group(limit=50):
    """Scrape a single group and save to DB."""
    group_id, group_url = pick_next_group()
    if not group_id:
        log("⚠️ No groups to scrape")
        return

    started = datetime.now().isoformat()
    log(f"📡 Scraping {group_id} ({group_url})...")

    try:
        posts = run_scraper(group_url, group_id, limit=limit)

        if not posts:
            log(f"  ⚠️ No posts returned (might not have access)")
            log_scrape(group_id, started, 0, 0, 0, "no_posts")
            return

        posts_new, comments_new = save_posts(group_id, posts)
        log(f"  ✅ {len(posts)} posts ({posts_new} new), {comments_new} new comments")
        log_scrape(group_id, started, posts_new, len(posts), comments_new)

    except subprocess.TimeoutExpired:
        log(f"  ⏰ Timeout (10 min limit)")
        log_scrape(group_id, started, 0, 0, 0, "error", "Timeout")
    except Exception as e:
        log(f"  ❌ Error: {e}")
        log_scrape(group_id, started, 0, 0, 0, "error", str(e)[:500])


def scrape_all_groups(limit=50):
    """Scrape all registered groups once."""
    conn = sqlite3.connect(str(DB_FILE))
    c = conn.cursor()
    c.execute("SELECT group_id, group_url FROM groups")
    groups = c.fetchall()
    conn.close()

    log(f"🔄 Scraping all {len(groups)} groups...")

    for i, (gid, url) in enumerate(groups):
        log(f"\n[{i+1}/{len(groups)}] {gid}")
        scrape_one_group(limit)

        # Random delay between groups (5-20 min)
        if i < len(groups) - 1:
            delay = random.randint(5 * 60, 20 * 60)
            log(f"  ⏳ Waiting {delay // 60} min before next group...")
            time.sleep(delay)

    log("\n✅ All groups scraped")


def run_daemon():
    """Continuous daemon — scrape random groups at random intervals."""
    log("🔄 Daemon mode started — will scrape random groups continuously")
    log(f"   Registered groups: {len(GROUP_IDS) + 1} (55 + subscriber hub)")

    while True:
        scrape_one_group(limit=50)

        delay = get_next_scrape_delay()
        mins = delay // 60
        secs = delay % 60
        log(f"  ⏳ Next scrape in {mins}m {secs}s")
        time.sleep(delay)


# ── Main ────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="FB Group Crawler Scheduler")
    parser.add_argument("--daemon", action="store_true", help="Run continuous daemon")
    parser.add_argument("--all", action="store_true", help="Scrape all groups once")
    parser.add_argument("--db-stats", action="store_true", help="Show database stats")
    parser.add_argument("--limit", type=int, default=50, help="Posts per group per scrape")
    parser.add_argument("--init", action="store_true", help="Initialize DB only")
    args = parser.parse_args()

    init_db()

    if args.db_stats:
        show_db_stats()
        return

    if args.init:
        print("✅ Database initialized")
        return

    if args.all:
        scrape_all_groups(limit=args.limit)
        return

    if args.daemon:
        run_daemon()
        return

    # Default: scrape one random group
    scrape_one_group(limit=args.limit)


if __name__ == "__main__":
    main()
