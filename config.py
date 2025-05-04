# config.py
BOT_TOKEN = "7961069001:AAFYJJB31CTnZy-HSPriGzwDaaJ4WKn-fv0"
SQLITE_DB_PATH = "telegrambot.db"

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

# --- General Toggles ---
ENABLE_DUPLICATE_DELETION = True
DUPLICATE_TIME_WINDOW_SECONDS = 3600
ENABLE_POINTS = True
ENABLE_TERMINAL_LOGGING = True
# --- End General Toggles ---
