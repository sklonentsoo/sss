from aiogram.filters import BaseFilter
from aiogram.types import Message
from config import ADMIN_IDS
from database import get_user

class RoleFilter(BaseFilter):
    def __init__(self, min_role: int):
        self.min_role = min_role

    async def __call__(self, message: Message) -> bool:
        if message.from_user.id in ADMIN_IDS:
            return True
        if not message.chat or message.chat.type == "private":
            return self.min_role == 0
            
        user_data = get_user(message.from_user.id, message.chat.id)
        user_role = user_data['role_id'] if user_data else 0
        return user_role >= self.min_role

class IsGroup(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return message.chat.type in ["group", "supergroup"]