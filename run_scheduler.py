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
import signal
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
    # "529408178379433", # PET TAXI CLUB PATTAYA — PRIVATE, join requested 3/10; enable after TK approves
    "LINEDEVTH",         # LINE Developers Group Thailand (61.9K, LINE OA/Messaging API intel, 1/10)
    "1720271421537650",  # หารถ เหมารถ รับส่ง ทั่วไทย (26K, BKR partner pool)
    "366971744331739",   # รถเหมาไปต่างจังหวัดทั่วประเทศ 24 ชม. (charter demand+fares, 27/9)
    "2853974944637528",  # ขนส่งสัตว์เลี้ยงสวยงามทั่วประเทศ (pet transport demand+fares, 27/9)
    "1040877324815287",  # เหมารถไปต่างจังหวัด (TK joined list, charter fares, 27/9)
    "414698031216363",   # รถรับส่ง หมาแมว และสัตว์เลี้ยง (pet transport demand, 27/9)
    "1253178929272755",  # หารถไปพัทยา/หัวหิน ต่างจังหวัด By Carrent (charter fares, 27/9)
    "330989769730010",   # กลุ่มหารถเช่าขับ Grab,Bolt,Taxi Meter (metered fare intel, 27/9)
    "392806644244430",   # Pets Giveaway Adopt Bangkok (pet transport demand in comments, 27/9)
    "882539605113201",   # Pets in Thailand lost/found/adoption (pet transport demand, 27/9)
    "2845817799007670",  # Thailand Expat Pet Owners Forum (pet transport demand, 27/9)
    # BDW demand-radar groups (wired 7/10, TK order "wire the demand radar to crawler") —
    # dog-walk/pet-sit demand feeds unified.db quality/NLP pipeline; demand_radar.py keeps its own sweep
    "722278746342756",   # หมาเจ้าปัญหา (Force-free) — walker recommendations, T0 (27/9)
    "657711149900356",   # หางานแม่บ้าน ดูแลสุนัข — pet-care hiring, T0 (27/9)
    "1038468616539109",  # dog walk & talk - bangkok — BDW core audience, T0 (27/9)
    "270081311694170",   # Pet Friendly Thailand — T0+ (27/9)
    "357008221822219",   # Dogs and Cats for Adoption TH — new-owner pet-care demand, T1 (27/9)
    "1206669499358683",  # Expats in Bangkok Forum — EN-speaking owner demand, T1 (27/9)
    "1121944334567453", "654748364354107", "1745892855948687", "933674975855737",
    "1774502687274720", "1001556964304386", "3894208190715125",
    "3229251420636884", "839534162744930",
    "7170559969654432", "770988810895106", "1576312052950752",
    "435434921238337", "3255595701139785", "1364459958209699",
    "267249817311678", "649245810623828",
    "778495273564899", "1712447172677146", "456882118879168", "490903803201153",
    "278105266564882", "1244175850623484", "882439515535971",
    "1238818407825761", "1342996746678532", "163583739075033",
    "276059605530869", "201997038548771", "181175949849139",
    "1884013731857192", "414485096343458", "2971164986495013", "487631723059303",
    "209932990505671", "510865431414449", "2317142971864047", "4087025951521595",
    "2495142260709549", "701472708866479", "456929628904615",
    "1049914571763738",
    # Added 2026-04-23 (topic expansion: ComfyUI)
    "484413594331346",
    # Added 2026-04-26
    "1500973233827126",
    # Added 2026-06-04 (user priority group)
    "1461988771737551",
    # Restored 2026-06-26: were pruned but accessible (low-activity, not dead)
    "2553120341751600",  # OpenCraft (12 posts, 6/6 success)
    "652178389264909",   # Home Assistant Ideas (40 posts, 5/6 success)
    "728630109211473",   # Solar 2 (5 posts, 5/6 success)
    "397412894378108",   # Aqara (12 posts, 6/6 success)
    "109767362376724",   # SAR Dogs (3 posts, 6/6 success)
    "325743041336807",   # SAR Foundation (7 posts, 6/6 success)
    "719862439125164",   # ad tech (24 posts, 5/6 success)
    "1668274093211179",  # ad tech 2 (60 posts, 6/6 success)
    "2254183738386576",  # Vibe Coding (6 posts, intermittent)
    "3682389062018307",  # pain group (2 posts, intermittent)
    "1577315533418837",  # OpenClaw (2 posts, intermittent)
    # Truly dead (not restored): 1244346110734837 (inaccessible)
    # ComfyUI/AI-media additions 2026-09-04 (joined-groups discovery)
    "1006512541072075",  # ComfyUI Thailand Community
    "1394143228095187",  # Stable Diffusion Korea (numeric ID; slug URL scrapes 0)

    "bkexpats.kc",  # BANGKOK EXPATS (BKR ads/expat reach, joined 4/10)
    "bangkokexpats",  # Bangkok Expats (BKR ads/expat reach, joined 4/10)
    "395501457186828",  # Bangkok Expat Families (BKR ads/expat reach, joined 4/10)
    "1089966954847972",  # Expats Living in Thailand (BKR ads/expat reach, joined 4/10)
    "travelthailandgroup",  # Thailand Travel Advice 🇹🇭 (BKR ads/expat reach, joined 4/10)
    "309599330111493",  # 🇹🇭 Thailand Travel Advice Group 🇹🇭 (BKR ads/expat reach, joined 4/10)
    "298606387906884",  # Thailand Travel Tips & Tricks (BKR ads/expat reach, joined 4/10)
    "digitalnomadsinthailand",  # Digital Nomads Thailand (BKR ads/expat reach, joined 4/10)
    # BKR tourist taxi-mining expansion 2026-10-06 (verified PUBLIC, from ads/fb_public_groups_taxi_mining.md)
    "touristhelpline",  # Tourist Helpline 316.6K — viral taxi/tuk-tuk scam warnings
    "bangkokinthailand",  # Bangkok in Thailand 77.7K — airport→city questions
    "bangkoktraveltips",  # Bangkok & TH Travel Tips 43.9K — first-time visitors
    "259145096730036",  # Bangkok Experience 60.5K — visitors/tourists
    "1742719962619719",  # Travel in Thailand 40.8K — transport Q&A
    "1084651901677407",  # Thailand Travel Bangkok 81.5K — package-tour transfer buyers
    "thailanddigitalnomads",  # Digital Nomads Thailand 92.1K — Grab/Bolt vs taxi debates
    "1497047133843898",  # Bangkok Digital Nomads (BKR ads/expat reach, joined 4/10)
    "googleadscommunity",  # Google Ads Community -AdWords Is Now Goo (BKR ads/expat reach, joined 4/10)
    "650236336729445",  # Meta Ads (Facebook, Instagram, WhatsApp) (BKR ads/expat reach, joined 4/10)
    "google.ads.facebook.ads.expe",  # Google Ads & Facebook Ads Experts Commun (BKR ads/expat reach, joined 4/10)
    "1163040338863052",  # Facebook Ads & Google Ads Services Exper (BKR ads/expat reach, joined 4/10)
    "195811799377722",  # Advertising Community Thailand (BKR ads/expat reach, joined 4/10)
    "137757067052822",  # Ad Addict - Sharing Room (BKR ads/expat reach, joined 4/10)
    "buyer12",  # Facebook and Google ads campaign expert (BKR ads/expat reach, joined 4/10)
    "375942032929720",  # Digital Marketing Thailand (BKR ads/expat reach, joined 4/10)
    "681574071631926",  # แจก Prompt Google Flow Thailand (BKR ads/expat reach, joined 4/10)
    "contentmarketing.th",  # หางานสาย Digital Marketing & Content Cre (BKR ads/expat reach, joined 4/10)
    "474411168020341",  # กลุ่มหางาน หาคน  Ecommerce / Digital Mar (BKR ads/expat reach, joined 4/10)
]

# Pages (not groups) — username-based URLs
# Pruned 2026-06-26: removed sardoginthailand (dead page)
PAGE_IDS = {}

# Supporter tabs — /<username>/subscribe/ URLs (paid creator subscription feeds).
# Added 2026-07-19 per user request. These use phase1_supporters_feed (separate parser).
# Note: Facebook creator subscriptions use /subscribe/ (not /supporters/ which 404s).
# Content visible to subscribers only — account must be a paying subscriber to see posts.
# Value = FB username (URL segment after facebook.com/). URL is built by scheduler.
SUPPORTER_PAGES = [
    # "earthh.evans.2025",  # DISABLED 2026-07-20: /supporters/ 404s, /subscribe/ requires paid sub (£0.99/mo)
]

SUBSCRIBER_HUB = ""  # Pruned 2026-06-26: subscriber hub inaccessible

# ── Anti-Detection Config ───────────────────────────────────────────
# Tuned 2026-06-26: EXTREME — 6+ rotations/day, shorter breaks, wider hours
# 56 groups × 5 rotations = 280 daily cap. Shorter intervals, tighter breaks.
# Headless=NO to ensure FB renders full feed (headless was causing 1-post yields).
# Auto-comment limits unchanged — those are the dominant bot-flag vector.
MAX_SCRAPES_PER_DAY = 450         # Aggressive mode (27/9, TK order): 64 groups × ~7 rotations
MIN_INTERVAL_MIN = 1              # Aggressive: 1-3 min between scrapes (was 3-8)
MAX_INTERVAL_MIN = 3
BREAK_EVERY_N = 40                # Long break less often
BREAK_MIN_MIN = 5                 # Shorter breaks (was 10-25 min)
BREAK_MAX_MIN = 10
SESSION_REFRESH_HOURS = 6         # Re-login interval
SLEEP_HOURS_START = 3             # Quiet hours start (AM)
SLEEP_HOURS_END = 5               # Quiet hours end (AM)
LOW_ACTIVITY_START = 2            # Low activity hours start
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
    for pid, purl in PAGE_IDS.items():
        crawler_db.ensure_source(
            "facebook", "page", pid, purl, "",
            metadata={"is_subscriber_hub": 0},
        )
    # Supporter tabs — /<username>/subscribe/ (FB creator subscription feeds)
    for username in SUPPORTER_PAGES:
        sup_url = f"https://www.facebook.com/{username}/subscribe/"
        crawler_db.ensure_source(
            "facebook", "supporters", username, sup_url, f"{username} (subscribers)",
            metadata={"is_subscriber_hub": 0, "parser": "supporters"},
        )
    # subscriber_hub removed 2026-06-26 (inaccessible)


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
    """Run scraper with incremental mode — passes known IDs for early exit.
    Auto-detects URL type: if URL ends with /supporters/, uses --type supporters."""
    import tempfile

    # Ensure DISPLAY is set for headed browser (Playwright headless=False)
    # TK 4/10: browser must NEVER pop on his desktop → default to virtual display :99 (Xvfb).
    if not os.environ.get("DISPLAY") or os.environ.get("DISPLAY") == ":0":
        os.environ["DISPLAY"] = ":77"
        r = subprocess.run(["bash", "-lc", "DISPLAY=:77 xset q >/dev/null 2>&1"], capture_output=True)
        if r.returncode != 0:
            subprocess.Popen(["Xvfb", ":77", "-screen", "0", "1280x1024x24", "-nolisten", "tcp"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(2)

    venv_python = BASE_DIR / "venv" / "bin" / "python3"
    # Use Camoufox scraper if --camoufox flag is set or env var is present
    use_camoufox = os.environ.get("FB_USE_CAMOUFOX", "0") == "1"
    scraper = BASE_DIR / ("scraper_camoufox.py" if use_camoufox else "scraper.py")
    limit = random.randint(80, 150)

    # Detect URL type — Supporters tabs use a separate parser
    # Matches both /supporters/ (legacy naming) and /subscribe/ (current FB URL)
    url_path = group_url.rstrip("/").split("/")[-1].lower()
    is_supporters = url_path in ("supporters", "subscribe", "subscription")
    url_type = "supporters" if is_supporters else "group"

    # Write known post IDs to temp file for incremental scraping
    sid = crawler_db.get_source_id("facebook", group_id)
    known = crawler_db.get_known_post_ids(sid) if sid else set()
    known_file = None
    # Base command — no auto-comment for supporters (paid feed, ToS risk)
    cmd = [str(venv_python), str(scraper),
           "--url", group_url,
           "--type", url_type,
           "--limit", str(limit), "--deep", "30", "--export", "json"]
    # Auto-comment PERMANENTLY DISABLED 2026-09-26 (TK: "dont comment under other
    # user's post if they dont mention"). Do not re-enable without TK's explicit order.
    # ponytail: env bypass removed entirely — the safe default is no writes.

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
                                timeout=2100, cwd=str(BASE_DIR))  # aggressive: 35 min — kills timeout-fails on deep=30 big groups (17 fails/6h)
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

def session_is_logged_out():
    """True if session_0.json lacks the c_user/xs login pair (public-only view).
    Found 2026-08-31: subscriber hub returned exactly 1 post for 25 days with status='success'
    because the logged-out feed renders a single visible post. Class fix: detect + alert."""
    try:
        cookies = json.loads((BASE_DIR / "session_0.json").read_text())
        names = {c.get("name") for c in cookies}
        return not ({"c_user", "xs"} <= names)
    except Exception:
        return True  # ponytail: missing/unreadable session treated as logged out; upgrade = vault auto-refresh


def scrape_one_group():
    """Scrape one group with full anti-detection."""
    # ── Block cool-down gate (2026-09-12): respect escalating backoff after
    # any detected checkpoint/limit. Skipping here keeps the daemon alive but idle.
    try:
        import blockguard
        if blockguard.in_cool_down():
            rem = blockguard.cool_down_remaining_min()
            log(f"🧊 Block cool-down active — {rem} min remaining. Skipping.")
            return False
    except Exception:
        pass

    if session_is_logged_out():
        # 2026-09-12 class fix: 7.5-day blind window (2,344 logged-out spins,
        # 34 hollow 'success' scrapes Sep 5→12). Never spin blind again —
        # attempt cookie autoheal from CDP vault before giving up.
        import cookie_autoheal
        if cookie_autoheal.try_heal():
            log("🩹 Session healed from vault — proceeding with scrape")
        else:
            log("🔒 Session LOGGED OUT and autoheal failed — skipping "
                "(Discord alerted, rate-limited)")
            return False

    if crawler_db.get_scrapes_today("facebook") >= MAX_SCRAPES_PER_DAY:
        log(f"⏸️ Daily limit reached ({MAX_SCRAPES_PER_DAY}/day). Waiting...")
        return False

    if is_quiet_hours():
        log(f"😴 Quiet hours ({SLEEP_HOURS_START}-{SLEEP_HOURS_END} AM). Skipping.")
        return False

    # Adaptive picker (TrendRadar/NewsNow pattern, 2026-04-21): per-source cooldown
    # multiplied by health — dry/errored sources drift back, healthy stay eligible.
    try:
        import adaptive_cooldown  # /home/tk578/crawlers/ on sys.path via import above
        picked = adaptive_cooldown.pick_next_source_adaptive(
            "facebook", host=os.environ.get("CRAWLER_HOST")
        )
    except Exception as _e:
        log(f"  ⚠️ Adaptive picker failed ({_e}); falling back to base picker")
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
    log(f"   Groups: {len(GROUP_IDS) + 1} | Pages: {len(PAGE_IDS)} | Supporters: {len(SUPPORTER_PAGES)}")
    log(f"   Max scrapes/day: {MAX_SCRAPES_PER_DAY}")
    log(f"   Interval: {MIN_INTERVAL_MIN}-{MAX_INTERVAL_MIN} min")
    log(f"   Quiet hours: {SLEEP_HOURS_START}-{SLEEP_HOURS_END} AM")
    log(f"   Headless: NO (real browser for anti-detection)")

    def signal_handler(signum, frame):
        """Handle SIGTERM and KeyboardInterrupt gracefully."""
        sig_name = signal.Signals(signum).name if signum else "KeyboardInterrupt"
        log(f"🛑 {sig_name} received — clean shutdown")
        sys.exit(0)

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    session_count = 0

    try:
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
    except KeyboardInterrupt:
        log("🛑 KeyboardInterrupt — clean shutdown")
        sys.exit(0)


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
