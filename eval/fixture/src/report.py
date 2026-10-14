from src.accounts import require_user
from src.core import normalize_name


def display_name(users: dict[str, dict], user_id: str) -> str:
    user = require_user(users, user_id)
    return normalize_name(user["name"]).title()


def can_read(users: dict[str, dict], user_id: str, role: str) -> bool:
    return role in require_user(users, user_id)["roles"]
