from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User


class LoginRequired(Exception):
    """Нет валидной сессии; обрабатывается редиректом на /auth/."""


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    if not user_id:
        raise LoginRequired()

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        request.session.clear()
        raise LoginRequired()
    return user
