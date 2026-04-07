#!/usr/bin/env python3
"""
Facebook Group Scraper v5 — Fast Feed Extraction

Single-pass feed scraping — no individual post navigation.
Extracts posts + comments + images directly from the group feed.

Usage:
  source venv/bin/activate
  python scraper.py --login
  python scraper.py --url <group_url> --comments --images --limit 100
"""

import asyncio
import json
import random
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

DATA_DIR = Path(__file__).parent / "data"
SESSION_FILE = Path(__file__).parent / "session.json"

STEALTH_JS = """
// Core: hide webdriver
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// Plugins
Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3, 4, 5].map(() => ({ name: 'Chrome PDF Plugin' }))
});

// Languages
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en', 'th'] });

// Chrome runtime
window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };

// Permissions
const oq = window.navigator.permissions.query;
window.navigator.permissions.query = (p) =>
    p.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : oq(p);

// WebGL
const gp = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(param) {
    if (param === 37445) return 'Google Inc. (NVIDIA)';
    if (param === 37446) return 'ANGLE (NVIDIA, NVIDIA GeForce RTX 5060 Ti, OpenGL 4.6)';
    return gp.call(this, param);
};
const gp2 = WebGL2RenderingContext.prototype.getParameter;
WebGL2RenderingContext.prototype.getParameter = function(param) {
    if (param === 37445) return 'Google Inc. (NVIDIA)';
    if (param === 37446) return 'ANGLE (NVIDIA, NVIDIA GeForce RTX 5060 Ti, OpenGL 4.6)';
    return gp2.call(this, param);
};

// Hardware concurrency (real CPU)
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });

// Platform
Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });

// Vendor
Object.defineProperty(navigator, 'vendor', { get: () => 'Google Inc.' });

// Connection (realistic)
Object.defineProperty(navigator, 'connection', {
    get: () => ({ effectiveType: '4g', rtt: 50, downlink: 10, saveData: false })
});

// Media codecs
Object.defineProperty(navigator, 'mediaCapabilities', {
    get: () => ({
        decodingInfo: () => Promise.resolve({ supported: true, powerEfficient: true, smooth: true })
    })
});

// Hide automation flags
delete navigator.__proto__.webdriver;

// Canvas fingerprint noise
const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
HTMLCanvasElement.prototype.toDataURL = function(type) {
    if (type === 'image/png') {
        const ctx = this.getContext('2d');
        if (ctx) {
            const imageData = ctx.getImageData(0, 0, this.width, this.height);
            for (let i = 0; i < imageData.data.length; i += 4) {
                imageData.data[i] ^= 1; // Tiny noise
            }
            ctx.putImageData(imageData, 0, 0);
        }
    }
    return origToDataURL.apply(this, arguments);
};

// Iframe contentWindow
const origContentWindow = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentWindow');
if (origContentWindow) {
    Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
        get: function() { return null; }
    });
}
"""


async def random_delay(lo=1.0, hi=2.5):
    await asyncio.sleep(random.uniform(lo, hi))


async def human_scroll(page):
    d = random.randint(300, 800)
    await page.evaluate(f"window.scrollBy(0, {d})")
    await random_delay(0.3, 0.8)
    # Random mouse movement
    x, y = random.randint(100, 800), random.randint(100, 600)
    await page.mouse.move(x, y, steps=random.randint(3, 10))
    await random_delay(0.1, 0.3)


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
    'shared', 'updated', 'was with', 'edited',
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


# ── Browser ─────────────────────────────────────────────────────────

async def create_browser(headless=False):
    pw = await async_playwright().start()
    args = [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-infobars",
        "--disable-extensions",
        "--disable-gpu",
        "--window-size=1280,800",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-component-extensions-with-background-pages",
    ]
    if headless:
        # Use Playwright headless (not --headless=new which can cause issues)
        pass  # handled below

    browser = await pw.chromium.launch(
        headless=headless,
        args=args,
    )
    ctx = await browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
        locale="en-US",
        timezone_id="Asia/Bangkok",
        extra_http_headers={
            "Accept-Language": "en-US,en;q=0.9,th;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        accept_downloads=True,
        bypass_csp=True,
        java_script_enabled=True,
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


# ── Feed Scraper (single pass) ─────────────────────────────────────

async def scrape_feed(page, group_url, limit=100, do_comments=False, do_images=False):
    """Scrape everything from the feed — no individual post navigation."""
    print(f"\n📡 Navigating to: {group_url}")
    await page.goto(group_url)
    await random_delay(3, 5)

    try:
        await page.wait_for_selector('[role="main"]', timeout=15000)
    except:
        print("⚠️ Page didn't load")
        return []

    posts = []
    seen_ids = set()
    stale = 0

    print(f"🎯 Target: {limit} posts\n")

    while len(posts) < limit:
        articles = await page.query_selector_all('[role="article"]')
        new = 0

        for article in articles:
            if len(posts) >= limit:
                break

            # ── Post ID + URL ──
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
                            post_url = f"https://www.facebook.com{href}" if href.startswith('/') else href
            except:
                pass

            if not post_id or post_id in seen_ids:
                continue

            seen_ids.add(post_id)
            new += 1

            # ── Author ──
            author = ""
            try:
                for link in (await article.query_selector_all('a[role="link"]'))[:5]:
                    txt = await safe_text(link)
                    if txt and 2 < len(txt) < 60 and not is_noise(txt):
                        author = txt
                        break
            except:
                pass

            # ── Timestamp ──
            timestamp = ""
            try:
                plink = await article.query_selector('a[href*="/posts/"]')
                if plink:
                    for s in await plink.query_selector_all('span'):
                        t = await safe_text(s)
                        if t and len(t) < 20:
                            timestamp = t
                            break
            except:
                pass

            # ── Text ──
            text = ""
            try:
                # Get all span[dir="auto"] — these are the text content blocks
                spans = await article.query_selector_all('span[dir="auto"]')
                clean = []
                for span in spans:
                    txt = await safe_text(span)
                    if txt and not is_noise(txt) and txt != author:
                        clean.append(txt)
                text = '\n'.join(clean)
            except:
                pass

            # ── Reactions ──
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

            # ── Image URLs ──
            image_urls = []
            if do_images:
                try:
                    for img in await article.query_selector_all('img[src*="scontent"]'):
                        src = await safe_attr(img, "src")
                        if src and "scontent" in src:
                            src = re.sub(r'/[sp]\d+x\d+/', '/o/', src)
                            image_urls.append(src)
                except:
                    pass

            # ── Video ──
            video_url = ""
            try:
                for v in await article.query_selector_all('video'):
                    src = await safe_attr(v, "src")
                    if src:
                        video_url = src
                        break
                if not video_url:
                    vl = await article.query_selector_all('a[href*="/watch/"], a[href*="/video/"]')
                    if vl:
                        video_url = await safe_attr(vl[0], "href")
            except:
                pass

            # ── Comments from feed ──
            comments = []
            if do_comments:
                try:
                    comment_divs = await article.query_selector_all('[aria-label*="Comment"]')
                    for cd in comment_divs[:20]:
                        ctext = await safe_text(cd)
                        parts = [p.strip() for p in ctext.split('|') if p.strip()]
                        clean_c = [p for p in parts if p and not is_noise(p)]
                        if len(clean_c) >= 2:
                            comments.append({
                                "author": clean_c[0],
                                "text": ' '.join(p for p in clean_c[1:] if not is_noise(p))
                            })
                except:
                    pass

            # ── Image content analysis ──
            image_content = []
            if do_images and image_urls:
                image_content = analyze_image_urls(image_urls[:5])

            posts.append({
                "id": post_id,
                "url": post_url,
                "author": author,
                "text": text,
                "timestamp": timestamp,
                "reactions": reactions,
                "image_urls": image_urls,
                "video_url": video_url,
                "image_content": image_content,
                "comments": comments,
                "scraped_at": datetime.now().isoformat(),
            })

            n = len(posts)
            preview = (text or '')[:70].replace('\n', ' ')
            ic = f" [{len(image_content)}img]" if image_content else ""
            cm = f" [{len(comments)}cmt]" if comments else ""
            print(f"  [{n}/{limit}] {author or '?'}{ic}{cm}: {preview}")

        await human_scroll(page)

        if new == 0:
            stale += 1
            if stale > 12:
                print(f"\n🛑 No new posts after 12 scrolls.")
                break
        else:
            stale = 0

    print(f"\n✅ Collected {len(posts)} posts")
    return posts


# ── Vision Analysis ─────────────────────────────────────────────────

def analyze_image_urls(urls):
    analyzer = Path(__file__).parent / "vision_analyze.py"
    if not analyzer.exists():
        return ["[analyzer not found]"]
    try:
        tmp = DATA_DIR / "_tmp_urls.json"
        tmp.write_text(json.dumps(urls))
        result = subprocess.run(
            [sys.executable, str(analyzer), str(tmp)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            return json.loads(result.stdout.strip())
        return [f"[error: {result.stderr[:200]}]"]
    except Exception as e:
        return [f"[error: {e}]"]


# ── Export ──────────────────────────────────────────────────────────

def export_data(posts, group_name, group_dir):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    group_dir.mkdir(parents=True, exist_ok=True)

    fp = group_dir / f"{ts}.json"
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(posts, f, ensure_ascii=False, indent=2)
    print(f"📁 JSON: {fp}")

    fp_md = group_dir / f"{ts}.md"
    with open(fp_md, "w", encoding="utf-8") as f:
        f.write(f"# {group_name} — {ts}\n\n")
        for i, p in enumerate(posts, 1):
            f.write(f"## Post {i}\n")
            f.write(f"**Author:** {p.get('author','?')} | "
                    f"**Date:** {p.get('timestamp','?')} | "
                    f"**Reactions:** {p.get('reactions',0)}\n")
            f.write(f"**URL:** {p.get('url','')}\n\n")
            if p.get("text"):
                f.write(f"{p['text']}\n\n")
            if p.get("image_content"):
                f.write("### Image Content\n")
                for ic in p["image_content"]:
                    f.write(f"- {ic}\n")
                f.write("\n")
            if p.get("video_url"):
                f.write(f"### Video\n`{p['video_url']}`\n\n")
            if p.get("comments"):
                f.write(f"### Comments ({len(p['comments'])})\n")
                for c in p["comments"]:
                    f.write(f"- **{c['author']}**: {c['text']}\n")
                f.write("\n")
            f.write("---\n\n")
    print(f"📁 Markdown: {fp_md}")

    summary = {
        "scraped_at": ts,
        "total_posts": len(posts),
        "total_comments": sum(len(p.get("comments", [])) for p in posts),
        "total_images": sum(len(p.get("image_urls", [])) for p in posts),
        "total_image_analyses": sum(len(p.get("image_content", [])) for p in posts),
    }
    sp = group_dir / f"{ts}_summary.json"
    sp.write_text(json.dumps(summary, indent=2))
    print(f"📁 Summary: {sp}")


# ── Main ────────────────────────────────────────────────────────────

async def main():
    import argparse
    parser = argparse.ArgumentParser(description="FB Group Scraper v5")
    parser.add_argument("--login", action="store_true")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--url", type=str)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--comments", action="store_true")
    parser.add_argument("--images", action="store_true")
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
            print("   python scraper.py --login")
            print("   python scraper.py --url 'https://www.facebook.com/groups/SLUG' --comments --images --limit 50")
            return

        posts = await scrape_feed(page, args.url, limit=args.limit,
                                  do_comments=args.comments, do_images=args.images)

        if posts:
            group_id = args.url.rstrip("/").split("/")[-1]
            group_dir = DATA_DIR / group_id
            if args.export in ("json", "both"):
                export_data(posts, group_id, group_dir)
            if args.export in ("markdown", "both") and args.export != "json":
                pass
            await save_session(ctx)
            total_cm = sum(len(p.get("comments", [])) for p in posts)
            total_im = sum(len(p.get("image_urls", [])) for p in posts)
            print(f"\n🎉 {len(posts)} posts, {total_cm} comments, {total_im} images → {group_dir}/")

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
