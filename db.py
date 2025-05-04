# db.py
import sqlite3
from config import SQLITE_DB_PATH

CREATE_TABLES_SQL = [
    '''
    CREATE TABLE IF NOT EXISTS live_posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        broadcast_id TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''',
    '''
    CREATE TABLE IF NOT EXISTS reactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id INTEGER NOT NULL,
        reactor_id INTEGER NOT NULL,
        stars INTEGER NOT NULL CHECK (stars BETWEEN 1 AND 5),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (post_id) REFERENCES live_posts (id) ON DELETE CASCADE
    )
    ''',
    '''
    CREATE TABLE IF NOT EXISTS score_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        delta INTEGER NOT NULL,
        reason TEXT NOT NULL,
        ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''',
    '''
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
    '''
]

def get_sqlite_conn():
    return sqlite3.connect(SQLITE_DB_PATH)

def initialize_sqlite_db():
    conn = get_sqlite_conn()
    cur = conn.cursor()
    for sql in CREATE_TABLES_SQL:
        cur.execute(sql)
    conn.commit()
    cur.close()
    conn.close()
