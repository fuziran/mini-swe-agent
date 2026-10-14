def require_user(users: dict[str, dict], user_id: str) -> dict:
    if user_id not in users:
        raise KeyError(user_id)
    return users[user_id]
