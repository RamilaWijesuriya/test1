from telegram.ext import ApplicationBuilder, MessageHandler, filters, MessageReactionHandler
from config import BOT_TOKEN, REACTION_SCORE_DEFERRED_SECONDS
from db import initialize_sqlite_db
from handlers import print_all_messages, handle_message_reaction
from scheduler import start_scheduler, deferred_emoji_scoring, daily_cleanup_and_leaderboard

if __name__ == "__main__":
    initialize_sqlite_db()
    print("SQLite database initialized.")
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.ALL, print_all_messages))
    app.add_handler(MessageReactionHandler(handle_message_reaction))
    # Start scheduler (hourly for deferred scoring, daily for leaderboard)
    start_scheduler(
        deferred_emoji_scoring,
        daily_cleanup_and_leaderboard,
        interval_seconds=REACTION_SCORE_DEFERRED_SECONDS,
        cron_kwargs={"hour": 0, "minute": 0, "timezone": "UTC"}
    )
    app.run_polling(allowed_updates=[
        "message_reaction",
        "message_reaction_count",
        "message",
        "edited_message"
    ])
