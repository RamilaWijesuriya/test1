# handlers.py
from telegram import Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes
import re
import asyncio
from config import ENABLE_POINTS, ENABLE_TERMINAL_LOGGING, REACTION_WEIGHTS, REACTION_REPLY_DELETE_SECONDS
from db import get_sqlite_conn

INSTAGRAM_LINK_REGEX = r"https?://(www\.)?instagram\.com/([^/]+)/live"

async def print_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    if chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
        sender_name = user.full_name if user else "Unknown"
        msg_text = update.message.text if update.message else "[non-text message]"
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
        # ...existing code for link detection and duplicate logic...

async def handle_message_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reaction_update = getattr(update, 'message_reaction', None)
    if not reaction_update:
        return
    chat = reaction_update.chat if hasattr(reaction_update, 'chat') else None
    user = reaction_update.user if hasattr(reaction_update, 'user') else None
    if not user or not chat:
        return
    reactor_id = user.id
    if chat.type not in ["group", "supergroup"]:
        return
    message_id = reaction_update.message_id if hasattr(reaction_update, 'message_id') else None
    post_id = message_id
    reactions = reaction_update.reaction if hasattr(reaction_update, 'reaction') else []
    for react in reactions:
        emoji = getattr(react, 'emoji', None)
        weight = REACTION_WEIGHTS.get(emoji)
        if not weight:
            continue
        conn = get_sqlite_conn()
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM live_posts WHERE id=?", (post_id,))
        row = cur.fetchone()
        if not row:
            cur.close()
            conn.close()
            continue
        submitter_id = row[0]
        if submitter_id == reactor_id:
            cur.close()
            conn.close()
            continue
        cur.execute("INSERT OR REPLACE INTO emoji_reactions (post_id, reactor_id, emoji, weight, ts) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)", (post_id, reactor_id, emoji, weight))
        cur.execute("INSERT INTO score_events (user_id, delta, reason, ts) VALUES (?, 1, 'reactor_emoji', CURRENT_TIMESTAMP)", (reactor_id,))
        conn.commit()
        cur.close()
        conn.close()
        try:
            mention_reactor = f"<a href='tg://user?id={reactor_id}'>reactor</a>"
            mention_submitter = f"<a href='tg://user?id={submitter_id}'>submitter</a>"
            reply = await context.bot.send_message(
                chat_id=chat.id,
                text=f"✅ +1 to {mention_reactor} and +{weight} to {mention_submitter}!",
                parse_mode='HTML',
                reply_to_message_id=post_id
            )
            await asyncio.sleep(REACTION_REPLY_DELETE_SECONDS)
            await reply.delete()
        except Exception as e:
            print(f"Failed to send/delete reaction reply: {e}")
