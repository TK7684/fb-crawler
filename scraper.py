#!/usr/bin/env python3
"""
Facebook Group Scraper v6 — Full Data Extraction

Hybrid approach:
  Phase 1: Fast feed scroll → collect post IDs + basic data
  Phase 2: Open individual posts → extract comments, images, videos, dates

Usage:
  source venv/bin/activate
  python scraper.py --login
  python scraper.py --url <group_url> --limit 50
"""

import asyncio
import json
import random
import re
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

DATA_DIR = Path(__file__).parent / "data"
SESSION_FILE = Path(__file__).parent / "session.json"

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3, 4, 5].map(() => ({ name: 'Chrome PDF Plugin' }))
});
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en', 'th'] });
window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };
const oq = window.navigator.permissions.query;
window.navigator.permissions.query = (p) =>
    p.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : oq(p);
const gp = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(param) {
    if (param === 37445) return 'Google Inc. (NVIDIA)';
    if (param === 37446) return 'ANGLE (NVIDIA, NVIDIA GeForce RTX 5060 Ti, OpenGL 4.6)';
    return gp.call(this, param);
};
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
Object.defineProperty(navigator, 'vendor', { get: () => 'Google Inc.' });
Object.defineProperty(navigator, 'connection', {
    get: () => ({ effectiveType: '4g', rtt: 50, downlink: 10, saveData: false })
});
delete navigator.__proto__.webdriver;
const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
HTMLCanvasElement.prototype.toDataURL = function(type) {
    if (type === 'image/png') {
        const ctx = this.getContext('2d');
        if (ctx) {
            const imageData = ctx.getImageData(0, 0, this.width, this.height);
            for (let i = 0; i < imageData.data.length; i += 4) { imageData.data[i] ^= 1; }
            ctx.putImageData(imageData, 0, 0);
        }
    }
    return origToDataURL.apply(this, arguments);
};
"""


async def random_delay(lo=1.0, hi=2.5):
    await asyncio.sleep(random.uniform(lo, hi))


async def human_scroll(page, slow=False):
    d = random.randint(300, 800) if not slow else random.randint(200, 400)
    await page.evaluate(f"window.scrollBy(0, {d})")
    await random_delay(0.3, 0.8)
    x, y = random.randint(100, 800), random.randint(100, 600)
    await page.mouse.move(x, y, steps=random.randint(3, 10))


async def safe_text(el):
    try:
        return (await el.inner_text()).strip()
    except:
        return ""


async def safe_attr(el, attr):
    try:
        return await el.get_attribute(attr) or ""
    except:
        return ""


UI_NOISE = {
    'like', 'reply', 'share', 'comment', 'most relevant', 'see more',
    'follow', 'send', 'more', 'joined', 'members', 'private group',
    'see all comments', 'view more comments', 'newest', 'all comments',
    'write a comment', 'comment...', 'like · reply',
    'edit', 'delete', 'pin post', 'turn off notifications', 'copy link',
    'send message', 'invite', 'share to feed', 'hide post', 'report post',
    'mark as spam', 'remove', 'undo', 'cancel', 'save', 'post',
    'gif', 'sticker', 'attachment', 'photo', 'video', 'tag', 'feeling',
    'check in', 'live video', 'poll', 'celebrate', 'unfollow', 'block',
    'see more reactions', 'reacted', 'and', 'others', 'commented',
    'shared', 'updated', 'was with', 'edited', 'replies', 'reply',
    'see more replies', 'view', 'likes', 'reactions', 'comment...',
    'sentences in thai', 'translated from thai',
    'was tagged in this', 'is with', 'feeling', 'feeling',
}

TS_RE = re.compile(
    r'^(\d+[hmd]?|just now|yesterday|last (?:week|month|year)|'
    r'\d+ (?:min|hour|hr|day|week|month|year)s? ago|\d{1,2}/\d{1,2}/\d{2,4})$', re.I
)


def is_noise(text):
    low = text.strip().lower()
    if not low or low == '·':
        return True
    if low in UI_NOISE:
        return True
    if TS_RE.match(low):
        return True
    if len(low) <= 2 and not any('\u0e00' <= c <= '\u0e7f' for c in text):
        return True
    return False


def parse_fb_timestamp(raw):
    """Convert FB relative/absolute timestamp to ISO date."""
    if not raw:
        return ""
    now = datetime.now()
    raw = raw.strip().lower()
    # Absolute dates: MM/DD/YY or DD/MM/YYYY
    for fmt in ("%m/%d/%y", "%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%d", "%d %B %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).isoformat()
        except:
            pass
    # Relative
    if raw == "just now":
        return now.isoformat()
    m = re.match(r'(\d+)\s*(min|minute|hour|hr|day|week|month|year)s?\s*ago', raw)
    if m:
        val, unit = int(m.group(1)), m.group(2)
        if 'min' in unit:
            return now.isoformat()
        elif 'h' in unit:
            from datetime import timedelta
            return (now - timedelta(hours=val)).isoformat()
        elif 'd' in unit:
            from datetime import timedelta
            return (now - timedelta(days=val)).isoformat()
        elif 'w' in unit:
            from datetime import timedelta
            return (now - timedelta(weeks=val)).isoformat()
        elif 'm' in unit:
            from datetime import timedelta
            return (now - timedelta(days=val*30)).isoformat()
        elif 'y' in unit:
            from datetime import timedelta
            return (now - timedelta(days=val*365)).isoformat()
    if raw == "yesterday":
        from datetime import timedelta
        return (now - timedelta(days=1)).isoformat()
    return raw


def download_file(url, cookies=None, timeout=15):
    """Download to temp file, return path."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
            "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
            "Referer": "https://www.facebook.com/",
        })
        if cookies:
            cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
            req.add_header("Cookie", cookie_str)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ct = resp.headers.get("Content-Type", "")
            ext = "jpg"
            if "png" in ct: ext = "png"
            elif "gif" in ct: ext = "gif"
            elif "webp" in ct: ext = "webp"
            elif "mp4" in ct: ext = "mp4"
            f = tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False)
            data = resp.read()
            if len(data) < 1000:  # Skip tiny responses
                f.close()
                Path(f.name).unlink()
                return None
            f.write(data)
            f.close()
            return f.name
    except Exception as e:
        return None


def ocr_image(filepath):
    """Run Tesseract OCR on image, return text."""
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(filepath)
        text = pytesseract.image_to_string(img, lang='tha+eng').strip()
        return text[:500] if text else ""
    except:
        return ""


def analyze_single_image(url, cookies=None):
    """Download + OCR a single image URL."""
    path = download_file(url, cookies=cookies)
    if not path:
        return f"[download failed: {url[:80]}]"
    ocr = ocr_image(path)
    # Clean up
    try:
        Path(path).unlink()
    except:
        pass
    if ocr:
        return f"OCR: {ocr[:300]}"
    # No OCR text — describe basic info
    try:
        from PIL import Image
        img = Image.open(path) if Path(path).exists() else None
        if img:
            return f"[image: {img.size[0]}x{img.size[1]}, no text]"
    except:
        pass
    return "[image: no text detected]"


# ── Browser ─────────────────────────────────────────────────────────

async def create_browser(headless=False):
    pw = await async_playwright().start()
    args = [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox", "--disable-dev-shm-usage",
        "--disable-infobars", "--disable-extensions", "--disable-gpu",
        "--window-size=1280,800",
        "--disable-background-timer-throttling",
        "--no-first-run", "--no-default-browser-check",
    ]
    browser = await pw.chromium.launch(headless=headless, args=args)
    ctx = await browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
        locale="en-US", timezone_id="Asia/Bangkok",
        extra_http_headers={
            "Accept-Language": "en-US,en;q=0.9,th;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        accept_downloads=True, bypass_csp=True,
    )
    if SESSION_FILE.exists():
        await ctx.add_cookies(json.loads(SESSION_FILE.read_text()))
        print("✅ Loaded saved session cookies")
    page = await ctx.new_page()
    await page.add_init_script(STEALTH_JS)
    return pw, browser, ctx, page


async def save_session(ctx):
    cookies = await ctx.cookies()
    SESSION_FILE.write_text(json.dumps(cookies, indent=2))
    print(f"✅ Session saved ({len(cookies)} cookies)")


async def do_login(page):
    print("\n🔐 Opening Facebook login...")
    await page.goto("https://www.facebook.com/login")
    await random_delay(2, 3)
    print("⏳ Log in manually. Press ENTER when done.")
    await asyncio.get_event_loop().run_in_executor(None, input)
    await page.goto("https://www.facebook.com/groups")
    await random_delay(2, 3)
    if "login" in page.url.lower():
        print("❌ Login failed.")
        return False
    print("✅ Login successful!")
    return True


# ── Phase 1: Feed Scroll (collect post IDs + basic data) ───────────

async def phase1_feed(page, group_url, limit):
    """Fast feed scroll to collect post IDs and visible data."""
    await page.goto(group_url)
    await random_delay(3, 5)

    try:
        await page.wait_for_selector('[role="main"]', timeout=15000)
    except:
        print("⚠️ Page didn't load")
        return []

    posts = {}
    seen = set()
    stale = 0
    scroll_count = 0

    while len(posts) < limit:
        articles = await page.query_selector_all('[role="article"]')
        new = 0

        for article in articles:
            if len(posts) >= limit:
                break
            # Get post ID
            post_id = ""
            post_url = ""
            try:
                link = await article.query_selector('a[href*="/posts/"]')
                if link:
                    href = await safe_attr(link, "href")
                    if href:
                        m = re.search(r'/posts/(\d+)', href)
                        if m:
                            post_id = m.group(1)
                            # Clean URL
                            clean_url = f"https://www.facebook.com/groups/{group_url.rstrip('/').split('/')[-1]}/posts/{post_id}/"
                            post_url = clean_url
            except:
                pass

            if not post_id or post_id in seen:
                continue

            seen.add(post_id)
            new += 1

            # Author
            author = ""
            try:
                for link in (await article.query_selector_all('a[role="link"]'))[:5]:
                    txt = await safe_text(link)
                    if txt and 2 < len(txt) < 60 and not is_noise(txt):
                        author = txt
                        break
            except:
                pass

            # Timestamp from the post link area
            timestamp_raw = ""
            try:
                spans = await article.query_selector_all('span')
                for s in spans:
                    t = await safe_text(s)
                    if t and len(t) < 30 and (TS_RE.match(t) or '/' in t or 'ago' in t.lower()
                        or 'yesterday' in t.lower() or 'just now' in t.lower()
                        or re.match(r'\d+ [hdm]', t)):
                        timestamp_raw = t
                        break
            except:
                pass

            # Text
            text = ""
            try:
                spans = await article.query_selector_all('span[dir="auto"]')
                clean = []
                for span in spans:
                    txt = await safe_text(span)
                    if txt and not is_noise(txt) and txt != author:
                        clean.append(txt)
                text = '\n'.join(clean)
            except:
                pass

            # Reactions count
            reactions = 0
            try:
                for el in await article.query_selector_all('span[aria-label]'):
                    label = await safe_attr(el, "aria-label")
                    if label and any(w in label.lower() for w in
                                   ['like','reaction','love','haha','wow','sad','angry','care']):
                        nums = re.findall(r'[\d,]+', label)
                        if nums:
                            reactions = int(nums[-1].replace(',', ''))
                            break
            except:
                pass

            # Comment count indicator
            comment_count = 0
            try:
                for el in await article.query_selector_all('span[aria-label]'):
                    label = await safe_attr(el, "aria-label")
                    if label and 'comment' in label.lower():
                        nums = re.findall(r'[\d,]+', label)
                        if nums:
                            comment_count = int(nums[0].replace(',', ''))
                            break
            except:
                pass

            posts[post_id] = {
                "id": post_id,
                "url": post_url,
                "author": author,
                "text": text,
                "timestamp": parse_fb_timestamp(timestamp_raw),
                "timestamp_raw": timestamp_raw,
                "reactions": reactions,
                "comment_count_hint": comment_count,
                "image_urls": [],
                "video_url": "",
                "image_content": [],
                "comments": [],
            }

        await human_scroll(page)
        scroll_count += 1

        if new == 0:
            stale += 1
            if stale > 15:
                break
        else:
            stale = 0
            n = len(posts)
            if n % 10 == 0:
                print(f"  Phase 1: {n}/{limit} posts collected...")

    print(f"✅ Phase 1: {len(posts)} posts from feed")
    return list(posts.values())


# ── Phase 2: Deep scrape individual posts (comments, media, dates) ─

async def phase2_deep(page, posts, max_deep=30, cookies=None):
    """Open each post page for comments, images, video, and accurate dates."""
    # Prioritize posts with more engagement
    posts.sort(key=lambda p: (p.get("comment_count_hint", 0), p.get("reactions", 0)), reverse=True)
    to_deep = posts[:max_deep]

    for i, post in enumerate(to_deep):
        pid = post["id"]
        url = post["url"]
        print(f"  Deep [{i+1}/{len(to_deep)}] Post {pid}...")

        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            if not resp or resp.status != 200:
                print(f"    ⚠️ Failed to load")
                await random_delay(2, 3)
                continue
        except Exception as e:
            print(f"    ⚠️ Error: {str(e)[:60]}")
            await random_delay(2, 3)
            continue

        await random_delay(2, 4)

        # --- Accurate timestamp ---
        try:
            # Try to find timestamp in the post header area
            for el in await page.query_selector_all('a[href*="/posts/"] span'):
                t = await safe_text(el)
                if t and len(t) < 40:
                    parsed = parse_fb_timestamp(t)
                    if parsed:
                        post["timestamp"] = parsed
                        post["timestamp_raw"] = t
                        break
        except:
            pass

        # --- Comments ---
        try:
            # Scroll down a bit to load comments
            await human_scroll(page, slow=True)
            await random_delay(1, 2)

            comment_blocks = await page.query_selector_all('[role="article"], [aria-label*="Comment"], [data-testid="UFI2Comment"]')
            comments = []
            for cb in comment_blocks[:30]:
                try:
                    # Try structured comment extraction
                    c_author = ""
                    c_text = ""
                    c_time = ""

                    # Author: look for links with user names
                    author_els = await cb.query_selector_all('a[role="link"]')
                    for a in author_els[:3]:
                        txt = await safe_text(a)
                        href = await safe_attr(a, "href")
                        if txt and not is_noise(txt) and len(txt) < 60 and '/groups/' not in (href or ''):
                            c_author = txt
                            break

                    # Text: all text content
                    all_text = await safe_text(cb)
                    lines = [l.strip() for l in all_text.split('\n') if l.strip()]
                    clean_lines = [l for l in lines if not is_noise(l) and l != c_author]

                    # Time: look for relative timestamps
                    for line in lines:
                        if TS_RE.match(line) or 'ago' in line.lower():
                            c_time = line
                            break

                    c_text = ' '.join(clean_lines)
                    if c_text and c_author:
                        comments.append({
                            "author": c_author,
                            "text": c_text[:1000],
                            "timestamp": parse_fb_timestamp(c_time) if c_time else "",
                            "timestamp_raw": c_time,
                        })
                except:
                    pass

            if comments:
                post["comments"] = comments
                print(f"    💬 {len(comments)} comments")
        except:
            pass

        # --- Images ---
        try:
            imgs = await page.query_selector_all('img[src*="scontent"]')
            image_urls = []
            seen_urls = set()
            for img in imgs[:15]:
                src = await safe_attr(img, "src")
                if src and "scontent" in src:
                    # Skip tiny icons/avatars (below 150px)
                    if any(s in src for s in ['s40x40', 's50x50', 's60x60', 's80x80']):
                        continue
                    # Clean URL to get larger version
                    src_clean = src.split('&')[0]  # Remove tracking params
                    base = src_clean.split('?')[0]
                    if base not in seen_urls:
                        seen_urls.add(base)
                        image_urls.append(src_clean)
            if image_urls:
                post["image_urls"] = image_urls
                print(f"    🖼️ {len(image_urls)} images")
        except:
            pass

        # --- Video ---
        try:
            video_url = ""
            for v in await page.query_selector_all('video source, video'):
                src = await safe_attr(v, "src")
                if src and len(src) > 20:
                    video_url = src
                    break
            if not video_url:
                for a in await page.query_selector_all('a[href*="/video/"]'):
                    href = await safe_attr(a, "href")
                    if href and '/reel/' not in href:
                        video_url = href if href.startswith('http') else f"https://www.facebook.com{href}"
                        break
            if video_url and '/reel/?' not in video_url:
                post["video_url"] = video_url
                print(f"    🎬 video found")
        except:
            pass

        # --- OCR images ---
        if post["image_urls"]:
            try:
                print(f"    🔍 OCR analyzing {min(len(post['image_urls']), 3)} images...")
                for img_url in post["image_urls"][:3]:
                    result = analyze_single_image(img_url, cookies=cookies)
                    if result:
                        post["image_content"].append(result)
            except Exception as e:
                post["image_content"].append(f"[OCR error: {str(e)[:100]}]")

        await random_delay(1.5, 3)

    return posts


# ── Export ──────────────────────────────────────────────────────────

def export_data(posts, group_name, group_dir):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    group_dir.mkdir(parents=True, exist_ok=True)

    # JSON
    fp = group_dir / f"{ts}.json"
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(posts, f, ensure_ascii=False, indent=2)
    print(f"📁 JSON: {fp}")

    # Markdown
    fp_md = group_dir / f"{ts}.md"
    with open(fp_md, "w", encoding="utf-8") as f:
        f.write(f"# {group_name} — {ts}\n\n")
        for i, p in enumerate(posts, 1):
            f.write(f"## Post {i}\n")
            f.write(f"**Author:** {p.get('author','?')} | "
                    f"**Date:** {p.get('timestamp','?')} ({p.get('timestamp_raw','')}) | "
                    f"**Reactions:** {p.get('reactions',0)}\n")
            f.write(f"**URL:** {p.get('url','')}\n\n")
            if p.get("text"):
                f.write(f"{p['text']}\n\n")
            if p.get("image_content"):
                f.write("### Image Content\n")
                for ic in p["image_content"]:
                    f.write(f"- {ic}\n")
                f.write("\n")
            if p.get("image_urls"):
                f.write(f"### Images ({len(p['image_urls'])})\n")
                for iu in p["image_urls"]:
                    f.write(f"- {iu}\n")
                f.write("\n")
            if p.get("video_url"):
                f.write(f"### Video\n`{p['video_url']}`\n\n")
            if p.get("comments"):
                f.write(f"### Comments ({len(p['comments'])})\n")
                for c in p["comments"]:
                    ts_c = f" ({c['timestamp_raw']})" if c.get('timestamp_raw') else ""
                    f.write(f"- **{c['author']}**{ts_c}: {c['text']}\n")
                f.write("\n")
            f.write("---\n\n")
    print(f"📁 Markdown: {fp_md}")

    # Summary
    summary = {
        "scraped_at": ts,
        "total_posts": len(posts),
        "posts_with_comments": sum(1 for p in posts if p.get("comments")),
        "total_comments": sum(len(p.get("comments", [])) for p in posts),
        "posts_with_images": sum(1 for p in posts if p.get("image_urls")),
        "total_images": sum(len(p.get("image_urls", [])) for p in posts),
        "total_ocr": sum(len(p.get("image_content", [])) for p in posts),
        "posts_with_video": sum(1 for p in posts if p.get("video_url")),
    }
    sp = group_dir / f"{ts}_summary.json"
    sp.write_text(json.dumps(summary, indent=2))
    print(f"📁 Summary: {sp}")


# ── Main ────────────────────────────────────────────────────────────

async def main():
    import argparse
    parser = argparse.ArgumentParser(description="FB Group Scraper v6")
    parser.add_argument("--login", action="store_true")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--url", type=str)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--deep", type=int, default=30, help="Max posts to deep scrape for comments")
    parser.add_argument("--export", choices=["json", "markdown", "both"], default="both")
    args = parser.parse_args()

    pw, browser, ctx, page = await create_browser(headless=args.headless)

    try:
        if args.login:
            if await do_login(page):
                await save_session(ctx)
            return

        if not args.url:
            print("❌ Provide --url or --login first")
            return

        # Phase 1: Fast feed
        posts = await phase1_feed(page, args.url, args.limit)

        if not posts:
            print("❌ No posts found")
            return

        # Phase 2: Deep scrape for comments, media, dates
        print(f"\n🔍 Phase 2: Deep scraping {min(len(posts), args.deep)} posts for comments & media...")
        fb_cookies = json.loads(SESSION_FILE.read_text()) if SESSION_FILE.exists() else None
        posts = await phase2_deep(page, posts, max_deep=args.deep, cookies=fb_cookies)

        # Export
        group_id = args.url.rstrip("/").split("/")[-1]
        group_dir = DATA_DIR / group_id
        export_data(posts, group_id, group_dir)
        await save_session(ctx)

        total_cm = sum(len(p.get("comments", [])) for p in posts)
        total_im = sum(len(p.get("image_urls", [])) for p in posts)
        total_ocr = sum(len(p.get("image_content", [])) for p in posts)
        total_vid = sum(1 for p in posts if p.get("video_url"))
        print(f"\n🎉 {len(posts)} posts, {total_cm} comments, {total_im} images ({total_ocr} OCR'd), {total_vid} videos → {group_dir}/")

    except KeyboardInterrupt:
        print("\n⚠️ Interrupted")
    except Exception as e:
        print(f"\n❌ {e}")
        import traceback
        traceback.print_exc()
    finally:
        await ctx.close()
        await browser.close()
        await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
