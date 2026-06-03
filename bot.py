import asyncio
import random
from datetime import datetime, timedelta
import pytz
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject, BaseFilter, ChatMemberUpdatedFilter, MEMBER_STATUS_CHANGED
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton, 
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, ChatMemberUpdated
)
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from config import BOT_TOKEN, ADMIN_IDS, ROLES, ROLE_NAMES, SUPPORT_LINK
from database import init_db, get_db, get_user, add_user_if_not_exists, reset_daily_activity
from filters import RoleFilter, IsGroup
from middlewares import ChatModerationMiddleware

bot = Bot(token=BOT_TOKEN, parse_mode="Markdown")
dp = Dispatcher(storage=MemoryStorage())

class ShopStates(StatesGroup):
    waiting_for_forward = State()

# --- ПЛАНИРОВЩИК СБРОСА СТАТИСТИКИ (00:00 MSK) ---
async def scheduler():
    tz = pytz.timezone("Europe/Moscow")
    while True:
        now = datetime.now(tz)
        tomorrow = now + timedelta(days=1)
        midnight = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 0, 0, 0, tzinfo=tz)
        wait_seconds = (midnight - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        reset_daily_activity()

# --- ХЭНДЛЕРЫ: СТАРТ И ОСНОВНОЕ МЕНЮ (Личные Сообщения) ---
@dp.message(Command("start"), F.chat.type == "private")
async def cmd_start(message: Message):
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛒 Shop"), KeyboardButton(text="💰 Balance")],
            [KeyboardButton(text="➕ Add to chat"), KeyboardButton(text="❓ Support")]
        ],
        resize_keyboard=True
    )
    await message.answer("👋 Привет! Я многофункциональный бот-модератор.\n🤖 Пропиши /help для списка команд.", reply_markup=kb)

@dp.message(F.chat.type == "private", F.text.in_({"🛒 Shop", "💰 Balance", "➕ Add to chat", "❓ Support"}))
async def menu_buttons(message: Message):
    if message.text == "🛒 Shop":
        await cmd_shop(message)
    elif message.text == "💰 Balance":
        await cmd_balance(message)
    elif message.text == "➕ Add to chat":
        await message.answer(f"Перейдите по ссылке, чтобы добавить меня в чат:\nhttps://t.me/{(await bot.get_me()).username}?startgroup=true")
    elif message.text == "❓ Support":
        await message.answer(f"✉️ Ссылка на техподдержку: {SUPPORT_LINK}")

# Заглушка на обычные сообщения в ЛС
@dp.message(F.chat.type == "private", ~F.text.startswith("/"))
async def private_fallback(message: Message):
    await message.answer("🤖 Пропиши /help для списка команд.")

# --- КОМАНДА /HELP ---
@dp.message(Command("help", "помощь", "команды"))
async def cmd_help(message: Message):
    text = (
        "📜 **Список команд бота:**\n\n"
        "📊 **Статистика:**\n"
        "• `/top`, `/топ` — Топ-5 активности чата за день.\n"
        "• `/stats`, `/стата` — Ваша личная статистика.\n\n"
        "⚔️ **Игры (Только в чатах):**\n"
        "• `/duel` — Испытать удачу в дуэли.\n"
        "• `/basketball` — Сыграть в баскетбол.\n"
        "• `/dice` — Бросить кубик.\n"
        "• `/game_stats` — Статистика Ваших игр.\n\n"
        "🛒 **Экономика:**\n"
        "• `/balance`, `/мойдум` — Ваш баланс валюты Дум.\n"
        "• `/shop`, `/магазин` — Магазин услуг.\n\n"
        "🛡️ **Модерация (От Братика и выше):**\n"
        "• `/mute`, `/мут` `[time]` `[reason]` — Мут пользователя.\n"
        "• `/unmute`, `/размут` — Снять мут.\n"
        "• `/warn`, `/варн` — Выдать предупреждение.\n"
        "• `/ban`, `/бан` — Забанить участника."
    )
    await message.answer(text)

# --- УПРАВЛЕНИЕ АКТИВНОСТЬЮ И ТОП ---
@dp.message(Command("top", "топ"))
async def cmd_top(message: Message):
    if message.chat.type == "private": return
    with get_db() as conn:
        rows = conn.execute(
            "SELECT username, messages_today, user_id FROM users WHERE chat_id = ? AND messages_today > 0 ORDER BY messages_today DESC LIMIT 5",
            (message.chat.id,)
        ).fetchall()
    
    if not rows:
        return await message.answer("📊 Сегодня в чате еще никто не писал!")
        
    text = "🏆 **Топ-5 активных пользователей за сегодня:**\n\n"
    for idx, row in enumerate(rows, 1):
        name = f"@{row['username']}" if row['username'] else f"ID: {row['user_id']}"
        text += f"{idx}. {name} — {row['messages_today']} сообщений\n"
    await message.answer(text)

# --- ЛИЧНАЯ И ОБЩАЯ СТАТИСТИКА ---
@dp.message(Command("stats", "стата", "my_stats", "моястата"))
async def cmd_stats(message: Message):
    chat_id = message.chat.id if message.chat.type != "private" else 0
    u = get_user(message.from_user.id, chat_id)
    if not u:
        return await message.answer("❌ Статистика не найдена. Напишите что-нибудь в чате!")
        
    with get_db() as conn:
        warns = conn.execute("SELECT COUNT(*) as c FROM warnings WHERE user_id = ? AND chat_id = ?", (message.from_user.id, chat_id)).fetchone()['c']
        
    role_name = ROLES.get(u['role_id'], "Ньюген")
    text = (
        f"📊 **Ваша статистика (Чат: {message.chat.title or 'ЛС'}):**\n"
        f"👑 Роль: `{role_name}`\n"
        f"💬 Сообщений сегодня: `{u['messages_today']}`\n"
        f"⚠️ Предупреждений: `{warns}/3`\n"
        f"💰 Баланс: `{u['balance']}` Дум"
    )
    await message.answer(text)

@dp.message(Command("statsbot", "статистикаббота"), RoleFilter(4))
async def cmd_statsbot(message: Message):
    with get_db() as conn:
        all_users = conn.execute("SELECT COUNT(DISTINCT user_id) as c FROM users").fetchone()['c']
        active_today = conn.execute("SELECT COUNT(DISTINCT user_id) as c FROM users WHERE messages_today > 0").fetchone()['c']
        media_count = conn.execute("SELECT COUNT(*) as c FROM memes_media").fetchone()['c']
        words_count = conn.execute("SELECT COUNT(*) as c FROM learned_words").fetchone()['c']
        chats_count = conn.execute("SELECT COUNT(*) as c FROM bot_chats").fetchone()['c']
        
    text = (
        "🤖 **Глобальная статистика бота:**\n\n"
        f"👥 Всего юзеров в базе: `{all_users}`\n"
        f"🔥 Активных сегодня: `{active_today}`\n"
        f"📁 Собрано медиа-файлов: `{media_count}`\n"
        f"🧠 Запомнено уникальных слов: `{words_count}`\n"
        f"💬 Чат-комнат подключено: `{chats_count}`"
    )
    await message.answer(text)

# --- ИГРОВОЙ БЛОК ---
@dp.message(Command("duel"), IsGroup())
async def cmd_duel(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Принять вызов ⚔️", callback_data=f"game_duel_{message.from_user.id}")
    ]])
    await message.answer(f"⚔️ Пользователь @{message.from_user.username} вызывает на дуэль! Нажмите кнопку ниже.", reply_markup=kb)

@dp.callback_query(F.data.startswith("game_duel_"))
async def callback_duel(call: CallbackQuery):
    owner_id = int(call.data.split("_")[2])
    if call.from_user.id == owner_id:
        return await call.answer("❌ Вы не можете играть с самим собой!", show_alert=True)
        
    p1_roll, p2_roll = random.randint(1, 6), random.randint(1, 6)
    w_id = owner_id if p1_roll > p2_roll else call.from_user.id
    
    with get_db() as conn:
        if p1_roll != p2_roll:
            l_id = call.from_user.id if w_id == owner_id else owner_id
            conn.execute("INSERT INTO game_stats (user_id, game_type, wins) VALUES (?, 'duel', 1) ON CONFLICT(user_id, game_type) DO UPDATE SET wins=wins+1", (w_id,))
            conn.execute("INSERT INTO game_stats (user_id, game_type, losses) VALUES (?, 'duel', 1) ON CONFLICT(user_id, game_type) DO UPDATE SET losses=losses+1", (l_id,))
            conn.commit()

    res_text = f"🎲 Результаты дуэли:\n@{call.message.reply_to_message.from_user.username if call.message.reply_to_message else 'Игрок 1'}: {p1_roll}\n@{call.from_user.username}: {p2_roll}\n\n"
    res_text += f"👑 Победил: " + (f"@{call.from_user.username}" if w_id == call.from_user.id else "Организатор") if p1_roll != p2_roll else "Ничья!"
    await call.message.edit_text(res_text)

# Баскетбол и кости (реализация по схожему принципу быстрого инлайна)
@dp.message(Command("basketball"), IsGroup())
async def cmd_basketball(message: Message):
    p1 = random.randint(0, 15)
    await message.answer(f"🏀 @{message.from_user.username} бросает мяч и получает **{p1}** очков!")

@dp.message(Command("dice"), IsGroup())
async def cmd_dice(message: Message):
    p1 = random.randint(1, 6)
    await message.answer(f"🎲 @{message.from_user.username} выбивает **{p1}**!")

@dp.message(Command("game_stats"))
async def cmd_game_stats(message: Message):
    with get_db() as conn:
        rows = conn.execute("SELECT game_type, wins, losses FROM game_stats WHERE user_id = ?", (message.from_user.id,)).fetchall()
    if not rows:
        return await message.answer("🎮 Вы еще не играли в игры.")
    text = "📊 **Ваша игровая статистика:**\n"
    for r in rows:
        text += f"• `{r['game_type']}`: Победил: {r['wins']} | Проиграл: {r['losses']}\n"
    await message.answer(text)

# --- ЭКОНОМИКА И МАГАЗИН УСЛУГ ---
@dp.message(Command("balance", "мойдум", "myduom"))
async def cmd_balance(message: Message):
    chat_id = message.chat.id if message.chat.type != "private" else 0
    u = get_user(message.from_user.id, chat_id)
    bal = u['balance'] if u else 0
    await message.answer(f"💰 Ваш баланс: `{bal}` Дум (1 Дум = 1 Telegram Star).")

@dp.message(Command("shop", "магазин"))
async def cmd_shop(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Снять мут (100 Дум)", callback_data="buy_unmute")],
        [InlineKeyboardButton(text="Снять варн (150 Дум)", callback_data="buy_unwarn")],
        [InlineKeyboardButton(text="Роль Фанат 24ч (50 Дум)", callback_data="buy_fan")],
        [InlineKeyboardButton(text="Безлимит мемов 1мес (250 Дум)", callback_data="buy_memes")]
    ])
    await message.answer("🛒 **Магазин услуг бота:**", reply_markup=kb)

@dp.callback_query(F.data.startswith("buy_"))
async def process_purchase(call: CallbackQuery, state: FSMContext):
    # Механика списания и запроса чата
    prices = {"buy_unmute": 100, "buy_unwarn": 150, "buy_fan": 50, "buy_memes": 250}
    service = call.data
    cost = prices.get(service, 99999)
    
    with get_db() as conn:
        # Для простоты ищем любой баланс юзера во всех чатах
        u = conn.execute("SELECT MAX(balance) as b FROM users WHERE user_id = ?", (call.from_user.id,)).fetchone()
        bal = u['b'] if u['b'] else 0
        
    if bal < cost:
        return await call.answer("❌ Недостаточно средств на балансе!", show_alert=True)
        
    await state.update_data(service=service, cost=cost)
    await state.set_state(ShopStates.waiting_for_forward)
    await call.message.answer("💳 Баланс проверен. Теперь **перешлите любое сообщение** из чата, в котором вы хотите применить услугу.")
    await call.answer()

@dp.message(ShopStates.waiting_for_forward)
async def service_applied(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    
    target_chat_id = None
    if message.forward_from_chat:
        target_chat_id = message.forward_from_chat.id
    else:
        # Попытка распарсить ID чата напрямую, если ввели цифрами
        try: target_chat_id = int(message.text)
        except: return await message.answer("❌ Не удалось распознать чат. Перешлите сообщение правильно.")
        
    # Списание и применение услуги
    with get_db() as conn:
        conn.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (data['cost'], message.from_user.id))
        if data['service'] == "buy_unmute":
            try:
                await bot.restrict_chat_member(target_chat_id, message.from_user.id, permissions=ChatPermissions(can_send_messages=True))
            except Exception: pass
        elif data['service'] == "buy_unwarn":
            conn.execute("DELETE FROM warnings WHERE id = (SELECT id FROM warnings WHERE user_id = ? AND chat_id = ? LIMIT 1)", (message.from_user.id, target_chat_id))
        conn.commit()
        
    await message.answer("✅ Успешно оплачено и применено к указанному чату!")

# --- СИСТЕМА ГЕНЕРАЦИИ МЕМОВ (/doom) ---
@dp.message(Command("doom", "mem", "rofl", "мем"))
async def cmd_doom(message: Message):
    with get_db() as conn:
        word_row = conn.execute("SELECT word FROM learned_words ORDER BY RANDOM() LIMIT 1").fetchone()
        media_row = conn.execute("SELECT file_id, file_type FROM memes_media ORDER BY RANDOM() LIMIT 1").fetchone()
        
    if not word_row:
        return await message.answer("🧠 Я еще не выучил ни одного слова!")
        
    word = word_row['word']
    if not media_row:
        return await message.answer(f"📝 {word}")
        
    f_id, f_type = media_row['file_id'], media_row['file_type']
    if f_type == 'photo':
        await message.answer_photo(f_id, caption=word)
    elif f_type == 'video':
        await message.answer_video(f_id, caption=word)
    elif f_type == 'animation':
        await message.answer_animation(f_id, caption=word)

# --- АДМИНИСТРАТИВНЫЙ БЛОК ОТЦА (Реклама и монеты) ---
@dp.message(Command("advert", "реклама"), RoleFilter(4))
async def cmd_advert(message: Message, command: CommandObject):
    if not command.args: return await message.answer("❌ Введите текст рекламы.")
    with get_db() as conn:
        chats = conn.execute("SELECT chat_id FROM bot_chats").fetchall()
        
    success = 0
    for c in chats:
        try:
            await bot.send_message(chat_id=c['chat_id'], text=command.args)
            success += 1
        except Exception: pass
    await message.answer(f"📢 Реклама успешно отправлена в `{success}` чатов.")

@dp.message(Command("addcoins", "начислитьдумы"), RoleFilter(4))
async def cmd_add_coins(message: Message, command: CommandObject):
    # Формат: /addcoins @username количество
    if not command.args: return
    args = command.args.split()
    if len(args) < 2: return
    username, amount = args[0].replace("@", ""), int(args[1])
    
    with get_db() as conn:
        conn.execute("UPDATE users SET balance = balance + ? WHERE username = ?", (amount, username))
        conn.commit()
    await message.answer(f"💰 Пользователю @{username} начислено `{amount}` Дум.")

# --- АВТОПОВЫШЕНИЕ СОЗДАТЕЛЯ ПРИ ДОБАВЛЕНИИ БОТА ---
@dp.my_chat_member(ChatMemberUpdatedFilter(member_status_changed))
async def on_bot_added(event: ChatMemberUpdated):
    if event.new_chat_member.status == "member":
        chat_id = event.chat.id
        with get_db() as conn:
            conn.execute("INSERT OR IGNORE INTO bot_chats (chat_id, title) VALUES (?, ?)", (chat_id, event.chat.title))
            conn.commit()
            
        try:
            admins = await event.chat.get_administrators()
            for adm in admins:
                if adm.status == "creator" and not adm.user.is_bot:
                    add_user_if_not_exists(adm.user.id, chat_id, adm.user.username, role_id=4)
                    await bot.send_message(adm.user.id, f"🏆 Вы назначены Отцом в чате *{event.chat.title}* как создатель чата.")
        except Exception: pass

# --- КЛАССИЧЕСКАЯ МОДЕРАЦИЯ (Мут, Бан, Варн) ---
@dp.message(Command("mute", "мут"), RoleFilter(2))
async def cmd_mute(message: Message, command: CommandObject):
    if not message.reply_to_message:
        return await message.answer("❌ Ответьте на сообщение пользователя, которого хотите замутить.")
    
    duration = 600  # 10 мин по дефолту
    if command.args:
        args = command.args.split()
        if "1h" in args[0]: duration = 3600
        elif "24h" in args[0]: duration = 86400
        
    target = message.reply_to_message.from_user
    until = int(time.time() + duration)
    try:
        await message.chat.restrict(user_id=target.id, permissions=None, until_date=until)
        await message.answer(f"🔇 Пользователь @{target.username} отправлен в мут на `{duration // 60}` минут.")
    except Exception as e:
        await message.answer(f"❌ Ошибка прав: {e}")

@dp.message(Command("ban", "бан"), RoleFilter(3))
async def cmd_ban(message: Message):
    if not message.reply_to_message: return
    target = message.reply_to_message.from_user
    try:
        await message.chat.ban(user_id=target.id)
        await message.answer(f"🚷 Пользователь @{target.username} успешно забанен.")
    except Exception as e: pass

# --- ЗАПУСК БОТА ---
async def main():
    init_db()
    dp.message.middleware(ChatModerationMiddleware())
    
    # Фоновые задачи
    asyncio.create_task(scheduler())
    
    print("🤖 Бот успешно запущен на Bothost!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
