# Handoff: Verify incremental scraping is paying off

**Date**: 2026-04-10 12:40 GMT+7
**Priority**: Medium
**Status**: Deployed, awaiting verification

## What was done

Shipped 4-phase incremental scraping optimization to both fb-crawler and x-crawler:
- **fb-crawler** commit `0eab9f5` → pushed → deployed to EC2 → daemon restarted
- **x-crawler** commit `cc81069` → pushed → deployed to EC2 → daemon restarted

Both daemons running. Schema migrations applied (verified `comment_count`, `reply_count`, `last_deep_scraped`, `posts_skipped` columns exist).

## What needs verification

1. **Tail logs after 24h** to confirm early-exit fires on mature groups:
   ```bash
   ssh -i "C:/Users/ttapk/Downloads/e2c-crawler.pem" ec2-user@18.142.43.20 \
     "tail -30 /home/ec2-user/fb-group-scraper/scheduler.log"
   ```
   Look for `(incremental, N known)` and `⏩ 10 consecutive known posts — stopping early`.

2. **Check `posts_skipped` ratio** in scrape_log — high values mean Phase A is working:
   ```sql
   SELECT group_id, AVG(posts_skipped*1.0/posts_total) FROM scrape_log
   WHERE posts_total > 0 AND started_at > datetime('now','-24 hours')
   GROUP BY group_id;
   ```

3. **Confirm export file count stays bounded** at ~5 per group:
   ```bash
   ls /home/ec2-user/fb-group-scraper/data/*/  | wc -l
   ```

4. **Watch for unexpected `posts_skipped == posts_total`** — could indicate the early-exit triggers immediately with no new posts (which is fine), but if persistent, verify scraping is still pulling new content.

## Open ideas

- Add `posts_skipped` to the Discord cron notification for visibility
- Long term: switch EC2 from `ec2-data` branch to a normal `git pull main` workflow with `data/` and `*.db` in `.gitignore` — current setup creates merge friction for code updates
