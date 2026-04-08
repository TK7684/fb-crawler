#!/usr/bin/env python3
"""Resolve FB group names — visit each group and extract the name from the page heading."""
import asyncio
import json
import random
import re
import sqlite3
from pathlib import Path
from playwright.async_api import async_playwright

BASE_DIR = Path(__file__).parent
SESSION_FILE = BASE_DIR / "session.json"
DB_FILE = BASE_DIR / "fb_groups.db"

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en', 'th'] });
window.chrome = { runtime: {} };
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
"""

NOISE_NAMES = {'Notifications', 'Facebook', 'Log In', 'Sign Up', 'Page Not Found', ''}


async def resolve_names():
    db = sqlite3.connect(str(DB_FILE))
    c = db.cursor()

    # Reset bad names
    c.execute("UPDATE groups SET group_name = '' WHERE group_name IN ('Notifications', 'Facebook')")
    db.commit()

    groups = c.execute("SELECT group_id, group_url FROM groups WHERE group_name = ''").fetchall()
    print(f"Groups to resolve: {len(groups)}")

    if not SESSION_FILE.exists():
        print("❌ No session file"); return

    cookies = json.loads(SESSION_FILE.read_text())
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
    )
    ctx = await browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
        locale="en-US", timezone_id="Asia/Bangkok",
    )
    await ctx.add_cookies(cookies)
    page = await ctx.new_page()
    await page.add_init_script(STEALTH_JS)

    resolved = 0
    failed = 0

    for gid, url in groups:
        try:
            resp = await page.goto(url, timeout=20000, wait_until="domcontentloaded")
            await asyncio.sleep(random.uniform(3, 5))

            # Check we're on the group page
            final_url = page.url
            if "/groups/" not in final_url and "/notifications" in final_url:
                print(f"  ⏭️ {gid}: redirected to notifications (not a member or private)")
                c.execute("UPDATE groups SET group_name = ? WHERE group_id = ?", ("(not accessible)", gid))
                db.commit()
                failed += 1
                continue

            name = ""

            # Method 1: og:title meta tag
            try:
                og = await page.query_selector('meta[property="og:title"]')
                if og:
                    og_content = await og.get_attribute("content") or ""
                    # og:title usually has "Group Name | Facebook groups"
                    parts = og_content.split("|")
                    name = parts[0].strip().strip('"').strip("'")
            except:
                pass

            # Method 2: First large heading on page
            if not name or name in NOISE_NAMES:
                try:
                    headings = await page.query_selector_all('h1, h2, [role="heading"][aria-level="1"]')
                    for h in headings[:5]:
                        t = await h.inner_text()
                        t = t.strip().strip('"').strip("'")
                        if t and t not in NOISE_NAMES and 3 < len(t) < 100:
                            name = t
                            break
                except:
                    pass

            # Method 3: The group title in the header area
            if not name or name in NOISE_NAMES:
                try:
                    # FB puts group name in a span inside the header
                    spans = await page.query_selector_all('[role="main"] span')
                    for s in spans[:20]:
                        t = await s.inner_text()
                        t = t.strip()
                        if t and t not in NOISE_NAMES and 4 < len(t) < 80 and not t.startswith('Join') and not t.startswith('Members'):
                            # Check if this looks like a group name (first meaningful text)
                            name = t
                            break
                except:
                    pass

            # Method 4: Page title (last resort)
            if not name or name in NOISE_NAMES:
                title = await page.title()
                if title:
                    # "Group Name | Groups | Facebook" or "Group Name"
                    parts = re.split(r'\s*[\|–—]\s*', title)
                    for p in parts:
                        p = p.strip().strip('"').strip("'")
                        if p and p not in NOISE_NAMES and p != "Groups" and "Facebook" not in p:
                            name = p
                            break

            if name and name not in NOISE_NAMES:
                c.execute("UPDATE groups SET group_name = ? WHERE group_id = ?", (name, gid))
                db.commit()
                resolved += 1
                print(f"  ✅ {gid}: {name}")
            else:
                print(f"  ⚠️ {gid}: could not resolve (url: {final_url[:60]})")
                failed += 1

            await asyncio.sleep(random.uniform(1.5, 3))

        except Exception as e:
            print(f"  ❌ {gid}: {str(e)[:60]}")
            failed += 1

    await ctx.close()
    await browser.close()
    await pw.stop()

    print(f"\n✅ Resolved: {resolved}/{len(groups)} | Failed: {failed}")

    print("\n--- All Groups ---")
    for r in c.execute("SELECT group_id, group_name, total_posts FROM groups ORDER BY total_posts DESC").fetchall():
        n = r[1] or "(unnamed)"
        print(f"  {r[0]}: {n} ({r[2]} posts)")

    db.close()


asyncio.run(resolve_names())
