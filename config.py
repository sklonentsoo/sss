import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "ВАШ_ТОКЕН")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "123456789").split(",") if x.strip()]
TIMEZONE = os.getenv("TIMEZONE", "Europe/Moscow")
SUPPORT_LINK = os.getenv("SUPPORT_LINK", "https://t.me/durov")

# Путь для Bothost persistent storage
DB_PATH = "/app/data/bot_database.db" if os.path.exists("/app/data") else "bot_database.db"

# Константы ролей
ROLES = {
    0: "Ньюген",
    1: "Фанат",
    2: "Братик",
    3: "Отчим",
    4: "Отец"
}

ROLE_NAMES = {v.lower(): k for k, v in ROLES.items()}