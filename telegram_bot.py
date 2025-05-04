import sys
import requests
import pandas as pd
import time
import sqlite3
import redis
from telegram import Update, Chat
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters, MessageReactionHandler
import re
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
import asyncio

# Replace with your bot token
BOT_TOKEN = "7961069001:AAFYJJB31CTnZy-HSPriGzwDaaJ4WKn-fv0"
# Replace with your chat ID (can be your user ID for direct messages)
CHAT_ID = "7461183862"

# SQLite database configuration
SQLITE_DB_PATH = "telegrambot.db"

# Database and Redis configuration
REDIS_HOST = "localhost"
REDIS_PORT = 6379
REDIS_DB = 0

# Table creation SQLs (for reference)
# live_posts: id SERIAL PRIMARY KEY, broadcast_id TEXT, user_id BIGINT, created_at TIMESTAMP
# reactions: id SERIAL PRIMARY KEY, post_id INTEGER REFERENCES live_posts(id), reactor_id BIGINT, stars INTEGER, created_at TIMESTAMP
# score_events: id SERIAL PRIMARY KEY, user_id BIGINT, delta INTEGER, reason TEXT, ts TIMESTAMP

def get_sqlite_conn():
    return sqlite3.connect(SQLITE_DB_PATH)

def get_redis_conn():
    return redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB)

def send_telegram_message(message: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message
    }
    response = requests.post(url, data=payload)
    if response.status_code == 200:
        print("Message sent successfully.")
        return response.json().get("result", {}).get("message_id")
    else:
        print("Failed to send message:", response.text)
        return None

def delete_telegram_message(message_id):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage"
    payload = {
        "chat_id": CHAT_ID,
        "message_id": message_id
    }
    response = requests.post(url, data=payload)
    if response.status_code == 200:
        print("Previous message deleted.")
    else:
        print("Failed to delete message:", response.text)

def get_names_from_sheet(sheet_csv_url):
    df = pd.read_csv(sheet_csv_url)
    names = df.iloc[:, 0].dropna().tolist()
    return names

def make_instagram_links(names):
    return [f"https://www.instagram.com/{name}/live/" for name in names]

BROADCAST_ID_REGEX = r"instagram.com/([^/]+)/live"

scheduler = BackgroundScheduler()

# Schedule average rating bonus 5 minutes after each post
async def schedule_avg_bonus(post_id, submitter_id):
    def avg_bonus_job():
        pg = get_sqlite_conn()
        cur = pg.cursor()
        cur.execute("SELECT stars FROM reactions WHERE post_id=? AND created_at <= ?", (post_id, datetime.utcnow() + timedelta(minutes=5)))
        stars = [row[0] for row in cur.fetchall()]
        if not stars:
            avg = 0
        else:
            avg = sum(stars) / len(stars)
        bonus = 0
        if avg >= 4.5:
            bonus = 4
        elif avg >= 4.0:
            bonus = 2
        elif avg >= 3.5:
            bonus = 1
        if bonus > 0:
            cur.execute("INSERT INTO score_events (user_id, delta, reason, ts) VALUES (?, ?, ?, CURRENT_TIMESTAMP)", (submitter_id, bonus, 'avg_bonus'))
            pg.commit()
        cur.close()
        pg.close()
        # Optionally notify submitter
    scheduler.add_job(avg_bonus_job, 'date', run_date=datetime.utcnow() + timedelta(minutes=5))

async def get_group_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type in [Chat.GROUP, Chat.SUPERGROUP]:
        group_id = chat.id
        await update.message.reply_text(f"This group's ID is: {group_id}")
        return group_id
    else:
        await update.message.reply_text("❌ This command can only be used in a group.")
        return None

async def post_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = await get_group_id(update, context)
    if not group_id:
        return
    user_id = update.effective_user.id
    text = update.message.text
    match = re.search(BROADCAST_ID_REGEX, text)
    if not match:
        await update.message.reply_text("❌ Please provide a valid Instagram Live link.")
        return
    broadcast_id = match.group(1)
    r = get_redis_conn()
    redis_key = f"recent_broadcasts:{broadcast_id}"
    if r.get(redis_key):
        await update.message.reply_text("⚠️ You can only share this broadcast once per hour.")
        # Log event here if needed
        return
    r.set(redis_key, user_id, ex=3600)
    pg = get_sqlite_conn()
    cur = pg.cursor()
    # Check if this is the first time ever
    cur.execute("SELECT 1 FROM live_posts WHERE broadcast_id=?", (broadcast_id,))
    first_picker = cur.fetchone() is None
    cur.execute("INSERT INTO live_posts (broadcast_id, user_id, created_at) VALUES (?, ?, CURRENT_TIMESTAMP) RETURNING id", (broadcast_id, user_id))
    post_id = cur.fetchone()[0]
    # Award points
    baseline_points = 1
    first_picker_points = 9 if first_picker else 0
    cur.execute("INSERT INTO score_events (user_id, delta, reason, ts) VALUES (?, ?, ?, CURRENT_TIMESTAMP)", (user_id, baseline_points, 'baseline'))
    if first_picker_points:
        cur.execute("INSERT INTO score_events (user_id, delta, reason, ts) VALUES (?, ?, ?, CURRENT_TIMESTAMP)", (user_id, first_picker_points, 'first_picker'))
    pg.commit()
    cur.close()
    pg.close()
    total_points = baseline_points + first_picker_points
    await update.message.reply_text(f"✅ Link accepted! You earned +{total_points} point(s).{' (First-Picker!)' if first_picker_points else ''}")
    await schedule_avg_bonus(post_id, user_id)
    # Log event here if needed

async def reaction_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_id = await get_group_id(update, context)
    if not group_id:
        return
    # Only process reactions to bot messages
    if not update.message or not update.message.reply_to_message:
        return
    post_message = update.message.reply_to_message
    user_id = update.effective_user.id
    stars = None
    # Parse stars from message (expecting: "/rate <stars>")
    if update.message.text and update.message.text.strip().startswith("/rate"):
        try:
            stars = int(update.message.text.strip().split()[1])
        except Exception:
            await update.message.reply_text("❌ Usage: /rate <1-5>")
            return
    if stars is None or not (1 <= stars <= 5):
        await update.message.reply_text("❌ Please provide a star rating between 1 and 5.")
        return
    # Get post_id from the original message (assume it's in the message text)
    m = re.search(r'post_id=(\d+)', post_message.text or "")
    if not m:
        await update.message.reply_text("❌ Could not find the post to rate.")
        return
    post_id = int(m.group(1))
    pg = get_sqlite_conn()
    cur = pg.cursor()
    # Get submitter
    cur.execute("SELECT user_id, created_at FROM live_posts WHERE id=?", (post_id,))
    row = cur.fetchone()
    if not row:
        await update.message.reply_text("❌ Post not found.")
        cur.close()
        pg.close()
        return
    submitter_id, post_created_at = row
    # Prevent self-rating and duplicate rating
    if user_id == submitter_id:
        await update.message.reply_text("❌ You cannot rate your own post.")
        cur.close()
        pg.close()
        return
    cur.execute("SELECT 1 FROM reactions WHERE post_id=? AND reactor_id=?", (post_id, user_id))
    if cur.fetchone():
        await update.message.reply_text("❌ You have already rated this post.")
        cur.close()
        pg.close()
        return
    # Record reaction
    cur.execute("INSERT INTO reactions (post_id, reactor_id, stars, created_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)", (post_id, user_id, stars))
    # Award points
    submitter_points = 2 if stars == 5 else 1 if stars == 4 else 0
    reactor_points = 1
    # Only award reactor points if within 5 minutes
    now = datetime.utcnow()
    if (now - post_created_at).total_seconds() <= 300:
        cur.execute("INSERT INTO score_events (user_id, delta, reason, ts) VALUES (?, ?, ?, CURRENT_TIMESTAMP)", (user_id, reactor_points, 'reactor'))
    if submitter_points:
        cur.execute("INSERT INTO score_events (user_id, delta, reason, ts) VALUES (?, ?, ?, CURRENT_TIMESTAMP)", (submitter_id, submitter_points, 'rating'))
    pg.commit()
    cur.close()
    pg.close()
    await update.message.reply_text(f"⭐️ Thanks for rating! You earned +1 point. Submitter earned +{submitter_points}.")
    # Optionally DM submitter
    # await context.bot.send_message(chat_id=submitter_id, text=f"You earned +{submitter_points} from a rating!")

DELAY_SECONDS = 120  # Change this value to set the delay in seconds

async def daily_cleanup_and_leaderboard():
    pg = get_sqlite_conn()
    cur = pg.cursor()
    # Calculate scores for the past 24h
    cur.execute('''
        SELECT user_id, SUM(delta) as total
        FROM score_events
        WHERE ts >= CURRENT_TIMESTAMP - INTERVAL '24 hours'
        GROUP BY user_id
        ORDER BY total DESC
    ''')
    scores = cur.fetchall()
    if not scores:
        cur.close()
        pg.close()
        return
    user_scores = {row[0]: row[1] for row in scores}
    user_ids = list(user_scores.keys())
    totals = list(user_scores.values())
    n = len(user_ids)
    top_10_count = max(1, n // 10)
    bottom_10_count = max(1, n // 10)
    # Top 10
    top_10 = user_ids[:top_10_count]
    # Bottom 10 (with total < 0)
    bottom_10 = [uid for uid in user_ids[-bottom_10_count:] if user_scores[uid] < 0]
    # Kick bottom 10
    for uid in bottom_10:
        # Optionally: context.bot.ban_chat_member(chat_id, uid)
        print(f"Kicked user {uid} for low performance.")
    # Publish leaderboard
    leaderboard = "🏆 Today’s Top 10:\n" + "\n".join([f"User {uid}: {user_scores[uid]}" for uid in top_10])
    send_telegram_message(leaderboard)
    cur.close()
    pg.close()

# Schedule daily cleanup at 00:00 UTC
from apscheduler.triggers.cron import CronTrigger
scheduler.add_job(daily_cleanup_and_leaderboard, CronTrigger(hour=0, minute=0, timezone='UTC'))

# Create tables for SQLite
CREATE_TABLES_SQL = [
    """
    CREATE TABLE IF NOT EXISTS live_posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        broadcast_id TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        message_id INTEGER,  -- Store Telegram message ID
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS reactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id INTEGER NOT NULL,
        reactor_id INTEGER NOT NULL,
        stars INTEGER NOT NULL CHECK (stars BETWEEN 1 AND 5),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (post_id) REFERENCES live_posts (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS score_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        delta INTEGER NOT NULL,
        reason TEXT NOT NULL,
        ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """
]

# Add emoji_reactions table for storing reactions
CREATE_TABLES_SQL.append('''
CREATE TABLE IF NOT EXISTS emoji_reactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL,
    reactor_id INTEGER NOT NULL,
    emoji TEXT NOT NULL,
    weight INTEGER NOT NULL,
    ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(post_id, reactor_id),
    FOREIGN KEY (post_id) REFERENCES live_posts (id) ON DELETE CASCADE
)
''')

def initialize_sqlite_db():
    conn = get_sqlite_conn()
    cur = conn.cursor()
    for sql in CREATE_TABLES_SQL:
        cur.execute(sql)
    conn.commit()
    cur.close()
    conn.close()

# Replace PostgreSQL connection functions with SQLite
get_pg_conn = get_sqlite_conn

# Define fetch_group_id command handler
async def fetch_group_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await get_group_id(update, context)

# --- Configurable Parameters ---
ENABLE_DUPLICATE_DELETION = True  # If True, delete duplicate links
DUPLICATE_TIME_WINDOW_SECONDS = 3600  # Time window for duplicate detection (in seconds)
ENABLE_POINTS = True  # If True, enable points system
ENABLE_TERMINAL_LOGGING = True  # If True, print messages and points in terminal
# Add more toggles as needed
# --- End Configurable Parameters ---

import re
from telegram.constants import ChatType

INSTAGRAM_LINK_REGEX = r"https?://(www\.)?instagram\.com/([^/]+)/live"

async def print_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    if chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
        sender_name = user.full_name if user else "Unknown"
        msg_text = update.message.text if update.message else "[non-text message]"
        # Points logic
        points = 0
        if ENABLE_POINTS:
            conn = get_sqlite_conn()
            cur = conn.cursor()
            cur.execute("SELECT SUM(delta) FROM score_events WHERE user_id=?", (user.id,))
            points = cur.fetchone()[0]
            points = points if points is not None else 0
            cur.close()
            conn.close()
        if ENABLE_TERMINAL_LOGGING:
            print(f"[{sender_name} | {points} pts]: {msg_text}")
        # Instagram link detection and ruleset
        match = re.search(INSTAGRAM_LINK_REGEX, msg_text)
        if match:
            broadcast_id = match.group(2)
            now = datetime.utcnow()
            conn = get_sqlite_conn()
            cur = conn.cursor()
            # Check for duplicate within time window
            cur.execute("SELECT id, created_at FROM live_posts WHERE broadcast_id=? ORDER BY created_at DESC LIMIT 1", (broadcast_id,))
            row = cur.fetchone()
            is_duplicate = False
            if row:
                last_time = datetime.strptime(row[1], "%Y-%m-%d %H:%M:%S") if isinstance(row[1], str) else row[1]
                if (now - last_time).total_seconds() < DUPLICATE_TIME_WINDOW_SECONDS:
                    is_duplicate = True
            if is_duplicate:
                if ENABLE_DUPLICATE_DELETION:
                    try:
                        await update.message.delete()
                        warn_msg = await update.effective_chat.send_message("⚠️ Duplicate link detected (within 1 hour window). No points awarded.")
                        await asyncio.sleep(5)
                        await warn_msg.delete()
                    except Exception as e:
                        print(f"Failed to delete duplicate or warning: {e}")
                else:
                    warn_msg = await update.message.reply_text("⚠️ Duplicate link detected (within 1 hour window). No points awarded.")
                    await asyncio.sleep(5)
                    await warn_msg.delete()
                cur.close()
                conn.close()
                return
            # Not a duplicate: record post and award points
            accepted_msg = await update.message.reply_text(f"✅ Link accepted! You earned points.")
            cur.execute("INSERT INTO live_posts (broadcast_id, user_id, message_id, created_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)", (broadcast_id, user.id, accepted_msg.message_id))
            if ENABLE_POINTS:
                # Baseline point
                cur.execute("INSERT INTO score_events (user_id, delta, reason, ts) VALUES (?, ?, ?, CURRENT_TIMESTAMP)", (user.id, 1, 'baseline'))
                # First picker bonus
                cur.execute("SELECT COUNT(*) FROM live_posts WHERE broadcast_id=?", (broadcast_id,))
                count = cur.fetchone()[0]
                if count == 1:
                    cur.execute("INSERT INTO score_events (user_id, delta, reason, ts) VALUES (?, ?, ?, CURRENT_TIMESTAMP)", (user.id, 10, 'first_picker'))
            conn.commit()
            cur.close()
            conn.close()
            await asyncio.sleep(5)
            await accepted_msg.delete()

async def print_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user:
        sender_name = user.full_name
        msg_text = update.message.text if update.message else "[non-text message]"
        # Check if this is a /rate command
        if msg_text and msg_text.strip().startswith("/rate"):
            try:
                stars = int(msg_text.strip().split()[1])
                if ENABLE_TERMINAL_LOGGING:
                    print(f"[REACTION] {sender_name} rated a post with {stars} star(s)")
            except Exception:
                pass

# Handler for logging message reactions (emoji reactions)
async def log_emoji_reactions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.reactions:
        user = update.effective_user
        sender_name = user.full_name if user else "Unknown"
        for reaction in update.message.reactions:
            # reaction.emoji gives the emoji, reaction.count gives the count
            print(f"[REACTION] {sender_name} reacted with {reaction.emoji} (id: {ord(reaction.emoji)})")

async def handle_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("Reaction update received:")
    print(update)

# --- Reaction Scoring Config ---
REACTION_WEIGHTS = {
    "💩": 1,
    "👎": 2,
    "👍": 3,
    "❤️": 4,
    "❤️‍🔥": 5
}
REACTION_REPLY_DELETE_SECONDS = 5
REACTION_SCORE_DEFERRED_SECONDS = 3600  # 1 hour
# --- End Reaction Scoring Config ---

# Handler for emoji reactions
async def handle_message_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("[DEBUG] handle_message_reaction called. Update:", update)
    reaction_update = getattr(update, 'message_reaction', None)
    if not reaction_update:
        print("[DEBUG] No message_reaction in update.")
        return
    chat = getattr(reaction_update, 'chat', None)
    user = getattr(reaction_update, 'user', None)
    if not user or not chat:
        print("[DEBUG] No user or chat in reaction_update.")
        return
    reactor_id = getattr(user, 'id', None)
    message_id = getattr(reaction_update, 'message_id', None)
    # Use new_reaction (tuple of ReactionTypeEmoji)
    reactions = getattr(reaction_update, 'new_reaction', tuple())
    for react in reactions:
        emoji = getattr(react, 'emoji', None)
        print(f"[REACTION] Emoji: {emoji} from user {reactor_id} on message {message_id}")
        weight = REACTION_WEIGHTS.get(emoji)
        if not weight:
            continue
        conn = get_sqlite_conn()
        cur = conn.cursor()
        # Use message_id to look up the post
        cur.execute("SELECT user_id FROM live_posts WHERE message_id=?", (message_id,))
        row = cur.fetchone()
        print(f"[DEBUG] live_posts row for message_id {message_id}: {row}")
        if not row:
            print(f"[WARNING] No live_posts entry found for message_id {message_id}. Notification will not be sent.")
            cur.close()
            conn.close()
            continue
        submitter_id = row[0]
        if submitter_id == reactor_id:
            cur.close()
            conn.close()
            continue
        # Add points immediately
        cur.execute("INSERT OR REPLACE INTO emoji_reactions (post_id, reactor_id, emoji, weight, ts) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)", (message_id, reactor_id, emoji, weight))
        cur.execute("INSERT INTO score_events (user_id, delta, reason, ts) VALUES (?, 1, 'reactor_emoji', CURRENT_TIMESTAMP)", (reactor_id,))
        cur.execute("INSERT INTO score_events (user_id, delta, reason, ts) VALUES (?, ?, 'reaction_emoji', CURRENT_TIMESTAMP)", (submitter_id, weight))
        conn.commit()
        cur.close()
        conn.close()
        # Notify both users in group, delete after 5 seconds
        try:
            mention_reactor = f"<a href='tg://user?id={reactor_id}'>reactor</a>"
            mention_submitter = f"<a href='tg://user?id={submitter_id}'>submitter</a>"
            reply = await context.bot.send_message(
                chat_id=chat.id,
                text=f"✅ +1 to {mention_reactor} and +{weight} to {mention_submitter}!",
                parse_mode='HTML',
                reply_to_message_id=message_id
            )
            await asyncio.sleep(REACTION_REPLY_DELETE_SECONDS)
            await reply.delete()
        except Exception as e:
            print(f"Failed to send/delete reaction reply: {e}")

# Scheduled job for deferred scoring
async def deferred_emoji_scoring():
    import math
    conn = get_sqlite_conn()
    cur = conn.cursor()
    # Find posts not yet awarded (no 'deferred_emoji' event)
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

# Schedule the deferred scoring job hourly
import asyncio
scheduler.add_job(lambda: asyncio.create_task(deferred_emoji_scoring()), 'interval', seconds=REACTION_SCORE_DEFERRED_SECONDS)

if __name__ == "__main__":
    initialize_sqlite_db()
    print("SQLite database initialized.")
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageReactionHandler(handle_message_reaction))
    app.add_handler(MessageHandler(filters.ALL, print_all_messages))
    # Start polling with allowed_updates set here
    app.run_polling(allowed_updates=[
        "message_reaction",
        "message_reaction_count",
        "message",
        "edited_message"
    ])
    scheduler.start()
