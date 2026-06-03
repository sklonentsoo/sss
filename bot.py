import os
import re
import time
import asyncio
import random
import sqlite3
import pytz
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.filters import Command, CommandObject, BaseFilter
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton, 
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, ChatMemberUpdated, ChatPermissions
)
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.client.default import DefaultBotProperties

# ==================== КОНФИГУРАЦИЯ ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
TIMEZONE = os.getenv("TIMEZONE", "Europe/Moscow")
SUPPORT_LINK = os.getenv("SUPPORT_LINK", "https://t.me/durov")

DB_PATH = "/app/data/bot_database.db" if os.path.exists("/app/data") else "bot_database.db"

ROLES = {0: "Ньюген", 1: "Фанат", 2: "Братик", 3: "Отчим", 4: "Отец"}
FLOOD_STORAGE = {}

class ShopStates(StatesGroup):
    waiting_for_forward = State()

# ==================== БАЗА ДАННЫХ ====================
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER, chat_id INTEGER, username TEXT, role_id INTEGER DEFAULT 0,
            messages_today INTEGER DEFAULT 0, balance INTEGER DEFAULT 0, PRIMARY KEY (user_id, chat_id)
        )""")
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, chat_id INTEGER, reason TEXT, timestamp TEXT
        )""")
        cursor.execute("CREATE TABLE IF NOT EXISTS banned_words (word TEXT PRIMARY KEY)")
        cursor.execute("CREATE TABLE IF NOT EXISTS whitelist_links (domain TEXT PRIMARY KEY)")
        cursor.execute("CREATE TABLE IF NOT EXISTS learned_words (word TEXT PRIMARY KEY, count INTEGER DEFAULT 1)")
        cursor.execute("CREATE TABLE IF NOT EXISTS memes_media (file_id TEXT PRIMARY KEY, file_type TEXT, timestamp TEXT)")
        cursor.execute("CREATE TABLE IF NOT EXISTS bot_chats (chat_id INTEGER PRIMARY KEY, title TEXT)")
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS game_stats (
            user_id INTEGER, game_type TEXT, wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0, PRIMARY KEY (user_id, game_type)
        )""")
        for domain in ['youtube.com', 'youtu.be', 'tiktok.com', 'instagram.com', 't.me']:
            cursor.execute("INSERT OR IGNORE INTO whitelist_links (domain) VALUES (?)", (domain,))
        conn.commit()

def get_user(user_id, chat_id):
    with get_db() as conn:
        res = conn.execute("SELECT * FROM users WHERE user_id = ? AND chat_id = ?", (user_id, chat_id)).fetchone()
        return dict(res) if res else None

def add_user_if_not_exists(user_id, chat_id, username=None, role_id=0):
    with get_db() as conn:
        conn.execute("""
            INSERT INTO users (user_id, chat_id, username, role_id) VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET username=excluded.username
        """, (user_id, chat_id, username, role_id))
        conn.commit()

# ==================== ФИЛЬТРЫ И МИДЛВАРИ ====================
class RoleFilter(BaseFilter):
    def __init__(self, min_role: int): self.min_role = min_role
    async def __call__(self, message: Message) -> bool:
        if message.from_user.id in ADMIN_IDS: return True
        if not message.chat or message.chat.type == "private": return self.min_role == 0
        u = get_user(message.from_user.id, message.chat.id)
        return (u['role_id'] if u else 0) >= self.min_role

class IsGroup(BaseFilter):
    async def __call__(self, message: Message) -> bool: return message.chat.type in ["group", "supergroup"]

class ChatModerationMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: Message, data):
        if not isinstance(event, Message) or not event.from_user or event.from_user.is_bot:
            return await handler(event, data)
        
        chat_id, user_id, username = event.chat.id, event.from_user.id, event.from_user.username
        
        if event.chat.type in ["group", "supergroup"]:
            add_user_if_not_exists(user_id, chat_id, username)
            with get_db() as conn:
                conn.execute("UPDATE users SET messages_today = messages_today + 1 WHERE user_id = ? AND chat_id = ?", (user_id, chat_id))
                conn.commit()
            
            f_id, f_type = None, None
            if event.photo: f_id, f_type = event.photo[-1].file_id, 'photo'
            elif event.video: f_id, f_type = event.video.file_id, 'video'
            elif event.animation: f_id, f_type = event.animation.file_id, 'animation'
            if f_id:
                with get_db() as conn:
                    conn.execute("INSERT OR IGNORE INTO memes_media (file_id, file_type, timestamp) VALUES (?, ?, ?)", (f_id, f_type, datetime.now().isoformat()))
                    conn.execute("DELETE FROM memes_media WHERE file_id NOT IN (SELECT file_id FROM memes_media ORDER BY timestamp DESC LIMIT 500)")
                    conn.commit()

            if event.text and not event.text.startswith('/'):
                words = re.findall(r'\b[a-zA-Zа-яА-ЯёЁ]{3,}\b', event.text.lower())
                with get_db() as conn:
                    for w in words:
                        conn.execute("INSERT INTO learned_words (word, count) VALUES (?, 1) ON CONFLICT(word) DO UPDATE SET count=count+1", (w,))
                    conn.commit()

            now = time.time()
            if chat_id not in FLOOD_STORAGE: FLOOD_STORAGE[chat_id] = {}
            if user_id not in FLOOD_STORAGE[chat_id]: FLOOD_STORAGE[chat_id][user_id] = []
            timestamps = [t for t in FLOOD_STORAGE[chat_id][user_id] if now - t <= 10] + [now]
            FLOOD_STORAGE[chat_id][user_id] = timestamps
            if len(timestamps) >= 10:
                try:
                    await event.chat.restrict(user_id=user_id, permissions=ChatPermissions(), until_date=int(time.time() + 600))
                    await event.reply("🚨 **Анти-флуд!** Мут на 10 минут за флуд.")
                    return
                except: pass

            text = event.text or event.caption or ""
            urls = re.findall(r'(https?://[^\s]+|(?:www\.)?[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})', text)
            if urls:
                with get_db() as conn:
                    whitelist = [r['domain'] for r in conn.execute("SELECT domain FROM whitelist_links").fetchall()]
                for url in urls:
                    dom = re.search(r'(?:https?://)?(?:www\.)?([^/\s]+)', url)
                    if dom and not any(d in dom.group(1).lower() for d in whitelist):
                        try:
                            await event.delete()
                            await event.chat.restrict(user_id=user_id, permissions=ChatPermissions(), until_date=int(time.time() + 3600))
                            await event.answer(f"⛔ @{username}, ссылки запрещены! Мут на 1 час.")
                            return
                        except: pass

            if event.text:
                with get_db() as conn:
                    banned = [r['word'] for r in conn.execute("SELECT word FROM banned_words").fetchall()]
                if any(w in event.text.lower() for w in banned):
                    try:
                        await event.delete()
                        with get_db() as conn:
                            conn.execute("INSERT INTO warnings (user_id, chat_id, reason, timestamp) VALUES (?, ?, ?, ?)", (user_id, chat_id, "Мат", datetime.now().isoformat()))
                            w_count = conn.execute("SELECT COUNT(*) as c FROM warnings WHERE user_id = ? AND chat_id = ?", (user_id, chat_id)).fetchone()['c']
                        if w_count >= 3:
                            await event.chat.ban(user_id=user_id)
                            await event.answer(f"🚷 @{username} забанен за 3/3 варна.")
                        else:
                            await event.answer(f"⚠️ @{username}, мат запрещен! Варн ({w_count}/3).")
                        return
                    except: pass

        return await handler(event, data)

# ==================== ХЭНДЛЕРЫ И КОМАНДЫ ====================
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="Markdown"))
dp = Dispatcher(storage=MemoryStorage())

async def scheduler():
    tz = pytz.timezone(TIMEZONE)
    while True:
        now = datetime.now(tz)
        tomorrow = now + timedelta(days=1)
        midnight = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 0, 0, 0, tzinfo=tz)
        await asyncio.sleep((midnight - now).total_seconds())
        with get_db() as conn:
            conn.execute("UPDATE users SET messages_today = 0")
            conn.commit()

@dp.message(Command("start"), F.chat.type == "private")
async def cmd_start(message: Message):
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🛒 Shop"), KeyboardButton(text="💰 Balance")],
        [KeyboardButton(text="➕ Add to chat"), KeyboardButton(text="❓ Support")]
    ], resize_keyboard=True)
    await message.answer("👋 Привет! Я бот-модератор.\nПропиши /help для списка команд.", reply_markup=kb)

@dp.message(F.chat.type == "private", F.text.in_({"🛒 Shop", "💰 Balance", "➕ Add to chat", "❓ Support"}))
async def menu_buttons(message: Message):
    if message.text == "🛒 Shop": await cmd_shop(message)
    elif message.text == "💰 Balance": await cmd_balance(message)
    elif message.text == "➕ Add to chat": await message.answer(f"Добавь меня: https://t.me/{(await bot.get_me()).username}?startgroup=true")
    elif message.text == "❓ Support": await message.answer(f"✉️ Поддержка: {SUPPORT_LINK}")

@dp.message(Command("help", "помощь", "команды"))
async def cmd_help(message: Message):
    await message.answer("📜 **Команды:**\n• `/top` — Активность за день\n• `/stats` — Моя стата\n• `/mute` — Мут (Модерация)\n• `/ban` — Бан (Модерация)\n• `/shop` — Магазин услуг\n• `/duel` — Игра дуэль")

@dp.message(Command("top", "топ"))
async def cmd_top(message: Message):
    if message.chat.type == "private": return
    with get_db() as conn:
        rows = conn.execute("SELECT username, messages_today, user_id FROM users WHERE chat_id = ? AND messages_today > 0 ORDER BY messages_today DESC LIMIT 5", (message.chat.id,)).fetchall()
    if not rows: return await message.answer("📊 Сегодня еще никто не писал!")
    text = "🏆 **Топ-5 за сегодня:**\n\n"
    for i, r in enumerate(rows, 1):
        name = f"@{r['username']}" if r['username'] else f"ID: {r['user_id']}"
        text += f"{i}. {name} — {r['messages_today']} собщ.\n"
    await message.answer(text)

@dp.message(Command("stats", "стата"))
async def cmd_stats(message: Message):
    u = get_user(message.from_user.id, message.chat.id if message.chat.type != "private" else 0)
    if not u: return await message.answer("❌ Напишите что-то в чате для учета статистики.")
    with get_db() as conn:
        warns = conn.execute("SELECT COUNT(*) as c FROM warnings WHERE user_id = ? AND chat_id = ?", (message.from_user.id, message.chat.id)).fetchone()['c']
    await message.answer(f"📊 **Ваша стата:**\n👑 Роль: `{ROLES.get(u['role_id'], 'Ньюген')}`\n💬 Сегодня сообщений: `{u['messages_today']}`\n⚠️ Варны: `{warns}/3`\n💰 Баланс: `{u['balance']}` Дум")

@dp.message(Command("duel"), IsGroup())
async def cmd_duel(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Принять вызов ⚔️", callback_data=f"game_duel_{message.from_user.id}")]])
    await message.answer(f"⚔️ @{message.from_user.username} вызывает на дуэль!", reply_markup=kb)

@dp.callback_query(F.data.startswith("game_duel_"))
async def callback_duel(call: CallbackQuery):
    owner_id = int(call.data.split("_")[2])
    if call.from_user.id == owner_id: return await call.answer("❌ Нельзя играть с собой!", show_alert=True)
    p1, p2 = random.randint(1, 6), random.randint(1, 6)
    w_id = owner_id if p1 > p2 else call.from_user.id
    with get_db() as conn:
        if p1 != p2:
            l_id = call.from_user.id if w_id == owner_id else owner_id
            conn.execute("INSERT INTO game_stats (user_id, game_type, wins) VALUES (?, 'duel', 1) ON CONFLICT(user_id, game_type) DO UPDATE SET wins=wins+1", (w_id,))
            conn.execute("INSERT INTO game_stats (user_id, game_type, losses) VALUES (?, 'duel', 1) ON CONFLICT(user_id, game_type) DO UPDATE SET losses=losses+1", (l_id,))
            conn.commit()
    await call.message.edit_text(f"🎲 Дуэль:\nОрганизатор: {p1}\nОппонент: {p2}\n\n🏆 Победитель: " + (f"@{call.from_user.username}" if p1 < p2 else "Организатор") if p1 != p2 else "Ничья!")

@dp.message(Command("balance", "мойдум"))
async def cmd_balance(message: Message):
    u = get_user(message.from_user.id, message.chat.id if message.chat.type != "private" else 0)
    await message.answer(f"💰 Ваш баланс: `{u['balance'] if u else 0}` Дум")

@dp.message(Command("shop", "магазин"))
async def cmd_shop(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Снять мут (100 Дум)", callback_data="buy_unmute")],
        [InlineKeyboardButton(text="Снять варн (150 Дум)", callback_data="buy_unwarn")]
    ])
    await message.answer("🛒 **Магазин услуг:**", reply_markup=kb)

@dp.callback_query(F.data.startswith("buy_"))
async def process_purchase(call: CallbackQuery, state: FSMContext):
    prices = {"buy_unmute": 100, "buy_unwarn": 150}
    service = call.data
    cost = prices.get(service, 99999)
    with get_db() as conn:
        u = conn.execute("SELECT MAX(balance) as b FROM users WHERE user_id = ?", (call.from_user.id,)).fetchone()
    if (u['b'] or 0) < cost: return await call.answer("❌ Недостаточно Дум!", show_alert=True)
    await state.update_data(service=service, cost=cost)
    await state.set_state(ShopStates.waiting_for_forward)
    await call.message.answer("💳 Перешлите любое сообщение из чата, где нужно применить услугу.")
    await call.answer()

@dp.message(ShopStates.waiting_for_forward)
async def service_applied(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    t_chat_id = message.forward_from_chat.id if message.forward_from_chat else None
    if not t_chat_id:
        try: t_chat_id = int(message.text)
        except: return await message.answer("❌ Не удалось распознать чат.")
    with get_db() as conn:
        conn.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (data['cost'], message.from_user.id))
        if data['service'] == "buy_unmute":
            try: await bot.restrict_chat_member(t_chat_id, message.from_user.id, permissions=ChatPermissions(can_send_messages=True))
            except: pass
        elif data['service'] == "buy_unwarn":
            conn.execute("DELETE FROM warnings WHERE id = (SELECT id FROM warnings WHERE user_id = ? AND chat_id = ? LIMIT 1)", (message.from_user.id, t_chat_id))
        conn.commit()
    await message.answer("✅ Успешно применено!")

@dp.message(Command("doom", "mem"))
async def cmd_doom(message: Message):
    with get_db() as conn:
        w = conn.execute("SELECT word FROM learned_words ORDER BY RANDOM() LIMIT 1").fetchone()
        m = conn.execute("SELECT file_id, file_type FROM memes_media ORDER BY RANDOM() LIMIT 1").fetchone()
    if not w: return await message.answer("🧠 База слов пуста.")
    if not m: return await message.answer(f"📝 {w['word']}")
    if m['file_type'] == 'photo': await message.answer_photo(m['file_id'], caption=w['word'])
    elif m['file_type'] == 'video': await message.answer_video(m['file_id'], caption=w['word'])
    elif m['file_type'] == 'animation': await message.answer_animation(m['file_id'], caption=w['word'])

@dp.message(Command("mute"), RoleFilter(2))
async def cmd_mute(message: Message, command: CommandObject):
    if not message.reply_to_message: return await message.answer("❌ Ответьте на сообщение юзера.")
    dur = 600
    if command.args and "1h" in command.args: dur = 3600
    elif command.args and "24h" in command.args: dur = 86400
    try:
        await message.chat.restrict(user_id=message.reply_to_message.from_user.id, permissions=ChatPermissions(), until_date=int(time.time() + dur))
        await message.answer("🔇 Юзер отправлен в мут.")
    except Exception as e: await message.answer(f"❌ Ошибка прав: {e}")

@dp.message(Command("ban"), RoleFilter(3))
async def cmd_ban(message: Message):
    if not message.reply_to_message: return
    try:
        await message.chat.ban(user_id=message.reply_to_message.from_user.id)
        await message.answer("🚷 Юзер забанен.")
    except: pass

@dp.my_chat_member()
async def on_bot_added(event: ChatMemberUpdated):
    if event.new_chat_member.status in ["member", "administrator"]:
        with get_db() as conn:
            conn.execute("INSERT OR IGNORE INTO bot_chats (chat_id, title) VALUES (?, ?)", (event.chat.id, event.chat.title))
            conn.commit()
        try:
            admins = await event.chat.get_administrators()
            for a in admins:
                if a.status == "creator" and not a.user.is_bot:
                    add_user_if_not_exists(a.user.id, event.chat.id, a.user.username, role_id=4)
                    await bot.send_message(a.user.id, f"🏆 Вы назначены Отцом в чате *{event.chat.title}*.")
        except Exception as e:
            print(f"Ошибка получения админов: {e}")

@dp.message(F.chat.type == "private")
async def private_fallback(message: Message):
    await message.answer("🤖 Пропиши /help для списка команд.")

async def main():
    init_db()
    dp.message.middleware(ChatModerationMiddleware())
    asyncio.create_task(scheduler())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
