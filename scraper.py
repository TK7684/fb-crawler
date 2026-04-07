#!/usr/bin/env python3
"""
Facebook Group Scraper v4 — Deep Extraction

Strategy:
1. Scroll feed → collect post IDs + basic info
2. Open each post individually → extract full content + comments + images
3. Vision analysis on images (OCR + description)

Usage:
  source venv/bin/activate
  python scraper.py --login
  python scraper.py --url <group_url> --limit 30
  python scraper.py --url <group_url> --all --limit 50
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
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3, 4, 5].map(() => ({ name: 'Chrome PDF Plugin' }))
});
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
window.chrome = { runtime: {} };
"""


async def random_delay(lo=1.5, hi=4.0):
    await asyncio.sleep(random.uniform(lo, hi))


async def human_scroll(page):
    d = random.randint(400, 900)
    await page.evaluate(f"window.scrollBy(0, {d})")
    await random_delay(0.5, 1.5)
    await page.mouse.move(random.randint(100, 800), random.randint(100, 600),
                          steps=random.randint(5, 15))


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
    'shared', 'updated', 'was with', 'edited', 'like reply', 'reply reply',
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
    browser = await pw.chromium.launch(
        headless=headless,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
    )
    ctx = await browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
        locale="en-US", timezone_id="Asia/Bangkok",
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        accept_downloads=True,
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


# ── Phase 1: Collect Post IDs from Feed ─────────────────────────────

async def collect_post_ids(page, group_url, limit):
    """Scroll the group feed and collect post IDs + author + preview text."""
    print(f"\n📡 Phase 1: Collecting post IDs from feed...")
    await page.goto(group_url)
    await random_delay(4, 6)

    try:
        await page.wait_for_selector('[role="main"]', timeout=15000)
    except:
        print("⚠️ Page didn't load")
        return []

    post_ids = []
    seen = set()
    stale = 0

    while len(post_ids) < limit:
        articles = await page.query_selector_all('[role="article"]')
        new = 0

        for article in articles:
            if len(post_ids) >= limit:
                break
            try:
                link = await article.query_selector('a[href*="/posts/"]')
                if not link:
                    continue
                href = await safe_attr(link, "href")
                m = re.search(r'/posts/(\d+)', href)
                if not m:
                    continue
                pid = m.group(1)
                if pid in seen:
                    continue
                seen.add(pid)

                # Quick preview
                author = ""
                text = ""
                try:
                    raw = await safe_text(article)
                    lines = [l.strip() for l in raw.split('\n') if l.strip() and not is_noise(l)]
                    for line in lines[:5]:
                        if not author and 2 < len(line) < 60:
                            author = line
                        elif not is_noise(line):
                            text = line
                            break
                except:
                    pass

                post_ids.append({"id": pid, "author": author, "preview": text})
                new += 1
                print(f"  Found [{len(post_ids)}/{limit}]: {author} — {text[:60]}")
            except:
                continue

        await human_scroll(page)

        if new == 0:
            stale += 1
            if stale > 12:
                break
        else:
            stale = 0

    print(f"✅ Collected {len(post_ids)} post IDs\n")
    return post_ids


# ── Phase 2: Deep Scrape Each Post ──────────────────────────────────

async def deep_scrape_post(page, group_id, post_info, do_images=False, do_comments=False):
    """Open a post individually and extract everything."""
    pid = post_info["id"]
    post_url = f"https://www.facebook.com/groups/{group_id}/posts/{pid}"

    result = {
        "id": pid,
        "url": post_url,
        "author": post_info.get("author", ""),
        "text": "",
        "timestamp": "",
        "reactions": 0,
        "image_urls": [],
        "video_url": "",
        "image_content": [],
        "comments": [],
        "scraped_at": datetime.now().isoformat(),
    }

    try:
        await page.goto(post_url)
        await random_delay(2, 4)
    except:
        return result

    # Get the main post content area
    article = await page.query_selector('[role="main"]')
    if not article:
        return result

    # ── Author (more accurate on individual post page) ──
    try:
        for link in (await article.query_selector_all('a[role="link"]'))[:8]:
            txt = await safe_text(link)
            if txt and 2 < len(txt) < 60 and not is_noise(txt):
                result["author"] = txt
                break
    except:
        pass

    # ── Timestamp ──
    try:
        post_link = await article.query_selector('a[href*="/posts/"]')
        if post_link:
            for s in await post_link.query_selector_all('span'):
                t = await safe_text(s)
                if t and len(t) < 20 and not is_noise(t):
                    result["timestamp"] = t
                    break
    except:
        pass

    # ── Full text (all paragraphs) ──
    try:
        # Get all text blocks from the post
        spans = await article.query_selector_all('span[dir="auto"]')
        clean_lines = []
        for span in spans:
            txt = await safe_text(span)
            if txt and not is_noise(txt) and txt != result["author"]:
                clean_lines.append(txt)
        result["text"] = '\n'.join(clean_lines)
    except:
        pass

    # ── Expand "See more" ──
    try:
        for _ in range(2):
            btn = await article.query_selector('div[role="button"]:has-text("See more")')
            if btn:
                await btn.click()
                await random_delay(0.8, 1.5)
            else:
                break
        # Re-extract after expansion
        spans = await article.query_selector_all('span[dir="auto"]')
        clean_lines = []
        for span in spans:
            txt = await safe_text(span)
            if txt and not is_noise(txt) and txt != result["author"]:
                clean_lines.append(txt)
        result["text"] = '\n'.join(clean_lines)
    except:
        pass

    # ── Reactions ──
    try:
        for el in await article.query_selector_all('span[aria-label]'):
            label = await safe_attr(el, "aria-label")
            if label and any(w in label.lower() for w in
                           ['like','reaction','love','haha','wow','sad','angry','care']):
                nums = re.findall(r'[\d,]+', label)
                if nums:
                    result["reactions"] = int(nums[-1].replace(',', ''))
                    break
    except:
        pass

    # ── Image URLs ──
    if do_images:
        try:
            imgs = await article.query_selector_all('img[src*="scontent"]')
            for img in imgs:
                src = await safe_attr(img, "src")
                if src and "scontent" in src:
                    src = re.sub(r'/[sp]\d+x\d+/', '/o/', src)
                    result["image_urls"].append(src)
        except:
            pass

    # ── Video ──
    try:
        for v in await article.query_selector_all('video'):
            src = await safe_attr(v, "src")
            if src:
                result["video_url"] = src
                break
        if not result["video_url"]:
            vl = await article.query_selector_all(
                'a[href*="/watch/"], a[href*="/video/"], a[href*="/reel/"]')
            if vl:
                result["video_url"] = await safe_attr(vl[0], "href")
    except:
        pass

    # ── Comments: expand then extract ──
    if do_comments:
        result["comments"] = await extract_comments(page, article)

    # ── Image content analysis ──
    if do_images and result["image_urls"]:
        result["image_content"] = analyze_image_urls(result["image_urls"][:5])

    return result


async def extract_comments(page, article, max_comments=100):
    """Expand and extract all comments from a post page."""
    comments = []

    # Expand "View more comments" multiple times
    for _ in range(5):
        try:
            btn = await article.query_selector(
                'div[role="button"]:has-text("View more comments"), '
                'div[role="button"]:has-text("See more comments"), '
                'div[role="button"]:has-text("ความคิดเห็นเพิ่มเติม"), '
                'div[role="button"]:has-text("ดูความคิดเห็น")'
            )
            if btn:
                await btn.click()
                await random_delay(1.5, 2.5)
            else:
                break
        except:
            break

    # Each comment is a div with aria-label containing "Comment"
    try:
        comment_divs = await article.query_selector_all('[aria-label*="Comment"]')
        for cd in comment_divs[:max_comments]:
            try:
                text = await safe_text(cd)
                # FB uses " | " separator in these divs: Author | text | timestamp | Like | Reply
                parts = [p.strip() for p in text.split('|') if p.strip()]
                clean = [p for p in parts if p and not is_noise(p)]
                if len(clean) >= 2:
                    author = clean[0]
                    body_parts = [p for p in clean[1:] if not is_noise(p)]
                    body = ' '.join(body_parts)
                    if body:
                        comments.append({"author": author, "text": body})
            except:
                continue
    except:
        pass

    return comments


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
                    f"**Date:** {p.get('timestamp','?')} | "
                    f"**Reactions:** {p.get('reactions',0)} | "
                    f"**ID:** {p.get('id','?')}\n\n")
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

    # Summary
    total_ic = sum(len(p.get("image_content", [])) for p in posts)
    total_cm = sum(len(p.get("comments", [])) for p in posts)
    total_imgs = sum(len(p.get("image_urls", [])) for p in posts)
    summary = {
        "scraped_at": ts,
        "total_posts": len(posts),
        "total_comments": total_cm,
        "total_images": total_imgs,
        "total_image_analyses": total_ic,
        "posts_with_comments": sum(1 for p in posts if p.get("comments")),
        "posts_with_images": sum(1 for p in posts if p.get("image_urls")),
    }
    sp = group_dir / f"{ts}_summary.json"
    sp.write_text(json.dumps(summary, indent=2))
    print(f"📁 Summary: {sp}")
    return summary


# ── Main ────────────────────────────────────────────────────────────

async def main():
    import argparse
    parser = argparse.ArgumentParser(description="FB Group Scraper v4")
    parser.add_argument("--login", action="store_true")
    parser.add_argument("--headless", action="store_true", help="Run browser headless (no window)")
    parser.add_argument("--url", type=str)
    parser.add_argument("--limit", type=int, default=50)
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
            print("   python scraper.py --url 'https://www.facebook.com/groups/SLUG' --comments --images --limit 30")
            return

        # Extract group ID from URL
        group_id = args.url.rstrip("/").split("/")[-1]

        # Create group directory
        group_dir = DATA_DIR / group_id
        group_dir.mkdir(parents=True, exist_ok=True)

        # Phase 1: Collect post IDs from feed (fast scroll)
        post_ids = await collect_post_ids(page, args.url, args.limit)
        if not post_ids:
            print("❌ No posts found")
            return

        # Phase 2: Deep scrape each post
        print(f"🔍 Phase 2: Deep scraping {len(post_ids)} posts...\n")
        posts = []
        for i, info in enumerate(post_ids):
            print(f"  [{i+1}/{len(post_ids)}] Scraping post {info['id']}...")
            post = await deep_scrape_post(
                page, group_id, info,
                do_images=args.images,
                do_comments=args.comments,
            )
            posts.append(post)

            n_cm = len(post["comments"])
            n_ic = len(post.get("image_content", []))
            n_im = len(post.get("image_urls", []))
            extras = []
            if n_cm:
                extras.append(f"{n_cm} comments")
            if n_im:
                extras.append(f"{n_im} images")
            if n_ic:
                extras.append(f"{n_ic} analyzed")
            ex_str = f" [{', '.join(extras)}]" if extras else ""
            print(f"         → {post.get('author', '?')}{ex_str}: {(post.get('text',''))[:60]}")

            await random_delay(1.5, 3.0)

        # Export
        print(f"\n📦 Exporting...")
        summary = export_data(posts, group_id, group_dir)

        await save_session(ctx)
        print(f"\n🎉 Done! {summary['total_posts']} posts, "
              f"{summary['total_comments']} comments, "
              f"{summary['total_images']} images, "
              f"{summary['total_image_analyses']} analyzed")
        print(f"   → {group_dir}/")

    except KeyboardInterrupt:
        print("\n⚠️ Interrupted")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await ctx.close()
        await browser.close()
        await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
