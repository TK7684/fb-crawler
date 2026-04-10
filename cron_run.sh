#!/bin/bash
# FB Group Crawler — cron wrapper with Discord updates
cd /home/tk578/.openclaw/workspace/fb-crawler

LOG="scheduler.log"
DISCORD_MSG=""

# Run scraper
OUTPUT=$(./venv/bin/python3 run_scheduler.py 2>&1)
echo "$OUTPUT" >> "$LOG"

# Extract stats from output
POSTS_NEW=$(echo "$OUTPUT" | grep "new," | tail -1 | grep -oP '\d+(?= new)' || echo "0")
COMMENTS=$(echo "$OUTPUT" | grep "comments" | tail -1 | grep -oP '\d+(?= comments)' || echo "0")
GROUP=$(echo "$OUTPUT" | grep "📡" | tail -1 | sed 's/.*\[\(.*\)\].*/\1/')
STATUS=$(echo "$OUTPUT" | grep -E "✅|❌|⚠️" | tail -1)

# Get DB stats
DB_STATS=$(./venv/bin/python3 -c "
import sqlite3
c=sqlite3.connect('fb_groups.db').cursor()
print(f'{c.execute(\"SELECT COUNT(*) FROM posts\").fetchone()[0]}')
print(f'{c.execute(\"SELECT COUNT(*) FROM comments\").fetchone()[0]}')
print(f'{c.execute(\"SELECT COUNT(*) FROM scrape_log WHERE status=\"success\"\").fetchone()[0]}')
" 2>/dev/null)

TOTAL_POSTS=$(echo "$DB_STATS" | sed -n '1p')
TOTAL_COMMENTS=$(echo "$DB_STATS" | sed -n '2p')
TOTAL_RUNS=$(echo "$DB_STATS" | sed -n '3p')

# Send Discord notification
python3 /home/tk578/.openclaw/workspace/scripts/discord_notify.py "facebook-group-scrape" "📊 **FB Crawler Update**
${STATUS}
**Scraped:** ${GROUP} — ${POSTS_NEW} new posts, ${COMMENTS} comments
**Database:** ${TOTAL_POSTS} posts, ${TOTAL_COMMENTS} comments total
**Runs:** ${TOTAL_RUNS} successful scrapes" 2>/dev/null
