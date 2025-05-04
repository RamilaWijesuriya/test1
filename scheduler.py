# scheduler.py
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import asyncio
from db import get_sqlite_conn
import math

def start_scheduler(deferred_emoji_scoring, daily_cleanup_and_leaderboard, interval_seconds, cron_kwargs):
    scheduler = BackgroundScheduler()
    scheduler.add_job(lambda: asyncio.create_task(deferred_emoji_scoring()), 'interval', seconds=interval_seconds)
    scheduler.add_job(daily_cleanup_and_leaderboard, CronTrigger(**cron_kwargs))
    scheduler.start()
    return scheduler

# Example deferred scoring job
async def deferred_emoji_scoring():
    conn = get_sqlite_conn()
    cur = conn.cursor()
    cur.execute('''
        SELECT lp.id, lp.user_id
        FROM live_posts lp
        LEFT JOIN score_events se ON se.reason='deferred_emoji' AND se.user_id=lp.user_id AND se.ts >= lp.created_at
        WHERE se.id IS NULL
    ''')
    posts = cur.fetchall()
    for post_id, submitter_id in posts:
        cur.execute("SELECT weight FROM emoji_reactions WHERE post_id=?", (post_id,))
        weights = [row[0] for row in cur.fetchall()]
        if not weights:
            continue
        points = math.ceil(sum(weights) / len(weights))
        cur.execute("INSERT INTO score_events (user_id, delta, reason, ts) VALUES (?, ?, 'deferred_emoji', CURRENT_TIMESTAMP)", (submitter_id, points))
    conn.commit()
    cur.close()
    conn.close()

# Example daily cleanup job
async def daily_cleanup_and_leaderboard():
    # ...existing code for leaderboard and cleanup...
    pass
