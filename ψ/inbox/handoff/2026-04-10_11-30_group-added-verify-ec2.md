# Handoff: Verify EC2 crawler picked up new group

**Date**: 2026-04-10 11:30 GMT+7
**From**: Session 2026-04-10
**Priority**: Low
**Status**: Awaiting verification

## What was done

- Added FB group `1049914571763738` to `GROUP_IDS` in both:
  - **Local**: `run_scheduler.py` (committed + pushed to GitHub)
  - **EC2** (`18.142.43.20`): `/home/ec2-user/fb-group-scraper/run_scheduler.py` (edited in-place via sed)

## What needs to happen next

1. **Verify EC2 scheduler is running** and will pick up the change:
   ```bash
   ssh -i "C:/Users/ttapk/Downloads/e2c-crawler.pem" ec2-user@18.142.43.20 "ps aux | grep scheduler; systemctl status fb-crawler 2>/dev/null"
   ```

2. **Consider git-based sync** — EC2 repo is at `/home/ec2-user/fb-group-scraper/` but local was renamed to `fb-crawler`. Manual edits on EC2 will drift. A `git pull` workflow would prevent this.

3. **Reconcile repo naming** — EC2 uses `fb-group-scraper`, local uses `fb-crawler`, GitHub is `fb-crawler`.

## EC2 Connection (saved to memory)

- **Key**: `C:\Users\ttapk\Downloads\e2c-crawler.pem`
- **User**: `ec2-user`
- **Host**: `18.142.43.20`
- **Path**: `/home/ec2-user/fb-group-scraper/`
