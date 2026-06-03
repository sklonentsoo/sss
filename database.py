import sqlite3
from datetime import datetime, timedelta
import pytz
from config import DB_PATH, TIMEZONE

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Таблица пользователей
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER,
            chat_id INTEGER,
            username TEXT,
            role_id INTEGER DEFAULT 0,
            messages_today INTEGER DEFAULT 0,
            balance INTEGER DEFAULT 0,
            meme_subscription_until TEXT,
            PRIMARY KEY (user_id, chat_id)
        )""")
        
        # Предупреждения
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            chat_id INTEGER,
            reason TEXT,
            timestamp TEXT
        )""")
        
        # Логи модерации
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS mod_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            moderator_id INTEGER,
            action TEXT,
            target_id INTEGER,
            reason TEXT,
            duration TEXT,
            timestamp TEXT
        )""")
        
        # Черный список слов
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS banned_words (
            word TEXT PRIMARY KEY
        )""")
        
        # Белый список доменов
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS whitelist_links (
            domain TEXT PRIMARY KEY
        )""")
        
        # Запомненные слова (счетчик встреч)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS learned_words (
            word TEXT PRIMARY KEY,
            count INTEGER DEFAULT 1
        )""")
        
        # Медиа для мемов
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS memes_media (
            file_id TEXT PRIMARY KEY,
            file_type TEXT,
            timestamp TEXT
        )""")
        
        # Чаты бота
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS bot_chats (
            chat_id INTEGER PRIMARY KEY,
            title TEXT
        )""")
        
        # Игровая статистика
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS game_stats (
            user_id INTEGER,
            game_type TEXT,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, game_type)
        )""")
        
        # Дефолтный белый список доменов
        default_domains = ['youtube.com', 'youtu.be', 'tiktok.com', 'instagram.com', 't.me']
        for domain in default_domains:
            cursor.execute("INSERT OR IGNORE INTO whitelist_links (domain) VALUES (?)", (domain,))
            
        conn.commit()

# --- Помощники БД ---
def get_user(user_id, chat_id):
    with get_db() as conn:
        res = conn.execute("SELECT * FROM users WHERE user_id = ? AND chat_id = ?", (user_id, chat_id)).fetchone()
        if res: return dict(res)
        return None

def add_user_if_not_exists(user_id, chat_id, username=None, role_id=0):
    with get_db() as conn:
        conn.execute("""
            INSERT INTO users (user_id, chat_id, username, role_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET username=excluded.username
        """, (user_id, chat_id, username, role_id))
        conn.commit()

def increment_activity(user_id, chat_id, username):
    with get_db() as conn:
        add_user_if_not_exists(user_id, chat_id, username)
        conn.execute("UPDATE users SET messages_today = messages_today + 1 WHERE user_id = ? AND chat_id = ?", (user_id, chat_id))
        conn.commit()

def reset_daily_activity():
    with get_db() as conn:
        conn.execute("UPDATE users SET messages_today = 0")
        conn.commit()