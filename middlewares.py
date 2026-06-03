import re
import time
from aiogram import BaseMiddleware
from aiogram.types import Message
from database import increment_activity, get_db, add_user_if_not_exists
import pytz
from datetime import datetime

# Хранилище для антифлуда: {chat_id: {user_id: [timestamps]}}
FLOOD_STORAGE = {}

class ChatModerationMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: Message, data):
        if not isinstance(event, Message) or not event.from_user or event.from_user.is_bot:
            return await handler(event, data)

        chat_id = event.chat.id
        user_id = event.from_user.id
        username = event.from_user.username
        
        # 1. Учет активности (Группы)
        if event.chat.type in ["group", "supergroup"]:
            increment_activity(user_id, chat_id, username)
            
            # Сохранение медиа для мемов
            file_id, file_type = None, None
            if event.photo:
                file_id, file_type = event.photo[-1].file_id, 'photo'
            elif event.video:
                file_id, file_type = event.video.file_id, 'video'
            elif event.animation:
                file_id, file_type = event.animation.file_id, 'animation'
                
            if file_id:
                with get_db() as conn:
                    conn.execute("INSERT OR IGNORE INTO memes_media (file_id, file_type, timestamp) VALUES (?, ?, ?)",
                                 (file_id, file_type, datetime.now().isoformat()))
                    # Лимит 500 файлов
                    conn.execute("DELETE FROM memes_media WHERE file_id NOT IN (SELECT file_id FROM memes_media ORDER BY timestamp DESC LIMIT 500)")
                    conn.commit()

            # Парсинг слов для мемов
            if event.text and not event.text.startswith('/'):
                words = re.findall(r'\b[a-zA-Zа-яА-ЯёЁ]{3,}\b', event.text.lower())
                with get_db() as conn:
                    for w in words:
                        conn.execute("INSERT INTO learned_words (word, count) VALUES (?, 1) ON CONFLICT(word) DO UPDATE SET count=count+1", (w,))
                    conn.commit()

            # 2. Анти-флуд система
            now = time.time()
            if chat_id not in FLOOD_STORAGE: FLOOD_STORAGE[chat_id] = {}
            if user_id not in FLOOD_STORAGE[chat_id]: FLOOD_STORAGE[chat_id][user_id] = []
            
            user_timestamps = FLOOD_STORAGE[chat_id][user_id]
            user_timestamps.append(now)
            # Очищаем старые таймстампы (>10 сек)
            user_timestamps = [t for t in user_timestamps if now - t <= 10]
            FLOOD_STORAGE[chat_id][user_id] = user_timestamps
            
            if len(user_timestamps) >= 10:
                try:
                    until = int(time.time() + 600)
                    await event.chat.restrict(user_id=user_id, permissions=None, until_date=until)
                    await event.reply("🚨 **Анти-флуд!** Вы получили мут на 10 минут за отправку 10 сообщений подряд.")
                    return
                except Exception:
                    pass

            # 3. Фильтр ссылок
            if event.text or event.caption:
                text = event.text or event.caption
                urls = re.findall(r'(https?://[^\s]+|(?:www\.)?[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})', text)
                if urls:
                    with get_db() as conn:
                        whitelist = [row['domain'] for row in conn.execute("SELECT domain FROM whitelist_links").fetchall()]
                    
                    for url in urls:
                        domain_match = re.search(r'(?:https?://)?(?:www\.)?([^/\s]+)', url)
                        if domain_match:
                            domain = domain_match.group(1).lower()
                            if not any(d in domain for d in whitelist):
                                try:
                                    await event.delete()
                                    until = int(time.time() + 3600)
                                    await event.chat.restrict(user_id=user_id, permissions=None, until_date=until)
                                    await event.answer(f"⛔ @{username}, ссылки запрещены! Мут на 1 час.")
                                    return
                                except Exception:
                                    pass

            # 4. Фильтр мата
            if event.text:
                with get_db() as conn:
                    banned_words = [row['word'] for row in conn.execute("SELECT word FROM banned_words").fetchall()]
                if any(w in event.text.lower() for w in banned_words):
                    try:
                        await event.delete()
                        # Выдача варна логика
                        with get_db() as conn:
                            conn.execute("INSERT INTO warnings (user_id, chat_id, reason, timestamp) VALUES (?, ?, ?, ?)",
                                         (user_id, chat_id, "Использование мата (Автофильтр)", datetime.now().isoformat()))
                            warn_count = conn.execute("SELECT COUNT(*) as c FROM warnings WHERE user_id = ? AND chat_id = ?", (user_id, chat_id)).fetchone()['c']
                            
                        if warn_count >= 3:
                            await event.chat.ban(user_id=user_id)
                            await event.answer(f"🚷 По首ьзователь @{username} забанен за 3/3 варна (мат).")
                        else:
                            await event.answer(f"⚠️ @{username}, мат запрещен! Варн ({warn_count}/3).")
                        return
                    except Exception:
                        pass

        return await handler(event, data)