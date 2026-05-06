import logging
import smtplib
import socket
import ssl
import time
from email.message import EmailMessage

from sqlalchemy import Select, delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import (
    activation_token_expiry,
    generate_reset_token,
    generate_salt,
    hash_password,
    hash_reset_token,
    now_utc,
    reset_token_expiry,
    verify_password,
)
from app.models.password_reset_token import PasswordResetToken
from app.models.account_activation_token import AccountActivationToken
from app.models.user import User

logger = logging.getLogger(__name__)


class AuthRateLimiter:
    def __init__(self, limit: int, window_seconds: int):
        self.limit = limit
        self.window_seconds = window_seconds
        self._attempts: dict[str, list[float]] = {}

    def is_allowed(self, key: str) -> bool:
        now = time.time()
        attempts = [ts for ts in self._attempts.get(key, []) if now - ts < self.window_seconds]
        if len(attempts) >= self.limit:
            self._attempts[key] = attempts
            return False
        attempts.append(now)
        self._attempts[key] = attempts
        return True


login_limiter = AuthRateLimiter(limit=6, window_seconds=300)
reset_limiter = AuthRateLimiter(limit=4, window_seconds=600)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def validate_password_strength(password: str) -> bool:
    if len(password) < 8:
        return False
    has_letter = any(ch.isalpha() for ch in password)
    has_digit = any(ch.isdigit() for ch in password)
    return has_letter and has_digit


def get_user_by_email(db: Session, email: str) -> User | None:
    stmt: Select[tuple[User]] = select(User).where(User.email == normalize_email(email))
    return db.execute(stmt).scalar_one_or_none()


def create_user(
    db: Session, email: str, full_name: str, password: str, *, is_active: bool = False
) -> User:
    salt = generate_salt()
    user = User(
        email=normalize_email(email),
        full_name=full_name.strip(),
        password_salt=salt,
        password_hash=hash_password(password=password, salt=salt),
        is_active=is_active,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    user = get_user_by_email(db=db, email=email)
    if not user:
        return None
    if not user.is_active:
        return None
    if not verify_password(password=password, salt=user.password_salt, password_hash=user.password_hash):
        return None
    return user


def is_user_pending_activation(db: Session, email: str) -> bool:
    user = get_user_by_email(db=db, email=email)
    return bool(user and not user.is_active)


def _build_reset_email(email: str, full_name: str, reset_link: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = "Восстановление пароля"
    msg["From"] = settings.smtp_sender or "no-reply@example.com"
    msg["To"] = email
    msg.set_content(
        f"Здравствуйте, {full_name}!\n\n"
        f"Для смены пароля перейдите по ссылке:\n{reset_link}\n\n"
        "Если вы не запрашивали восстановление, просто проигнорируйте это письмо.\n"
        "Ссылка действительна 30 минут.",
        charset="utf-8",
    )
    return msg


def _smtp_send_message(message: EmailMessage) -> bool:
    if not settings.smtp_host or not settings.smtp_sender:
        return False

    sender = (settings.smtp_sender or "").strip()
    username = (settings.smtp_username or "").strip()
    ctx = ssl.create_default_context()
    envelope_from = sender or username or "no-reply@localhost"
    try:
        if settings.smtp_use_ssl:
            with smtplib.SMTP_SSL(
                settings.smtp_host,
                settings.smtp_port,
                timeout=30,
                context=ctx,
            ) as smtp:
                if settings.smtp_username and settings.smtp_password:
                    smtp.login(settings.smtp_username, settings.smtp_password)
                smtp.send_message(message, from_addr=envelope_from)
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
                if settings.smtp_use_tls:
                    smtp.starttls(context=ctx)
                if settings.smtp_username and settings.smtp_password:
                    smtp.login(settings.smtp_username, settings.smtp_password)
                smtp.send_message(message, from_addr=envelope_from)
        return True
    except smtplib.SMTPAuthenticationError as exc:
        logger.error(
            "SMTP: логин/пароль отклонены (%s). Для Gmail: SMTP_USERNAME = полный адрес "
            "(you@gmail.com), SMTP_PASSWORD = пароль приложения "
            "(https://myaccount.google.com/apppasswords), не пароль от аккаунта. "
            "SMTP_SENDER = тот же адрес. Детали: %s",
            settings.smtp_host,
            exc,
        )
        return False
    except socket.gaierror:
        logger.error(
            "SMTP: не удалось найти сервер «%s» (ошибка DNS). "
            "В .env задайте реальный SMTP_HOST.",
            settings.smtp_host,
        )
        return False
    except Exception:
        logger.exception(
            "Ошибка SMTP при отправке письма (host=%s port=%s tls=%s ssl=%s)",
            settings.smtp_host,
            settings.smtp_port,
            settings.smtp_use_tls,
            settings.smtp_use_ssl,
        )
        return False


def send_password_reset_email(email: str, full_name: str, reset_link: str) -> bool:
    if not settings.smtp_host or not settings.smtp_sender:
        logger.warning(
            "Письмо сброса пароля не отправлено: задайте SMTP_HOST и SMTP_SENDER в .env "
            "и перезапустите приложение."
        )
        return False

    sender = (settings.smtp_sender or "").strip()
    username = (settings.smtp_username or "").strip()
    if username and sender and normalize_email(username) != normalize_email(sender):
        logger.warning(
            "SMTP: SMTP_USERNAME и SMTP_SENDER различаются (%r vs %r). "
            "У Gmail/Yandex отправитель обычно должен совпадать с логином.",
            username,
            sender,
        )

    message = _build_reset_email(email=email, full_name=full_name, reset_link=reset_link)
    sent = _smtp_send_message(message)
    if sent:
        logger.info("Письмо сброса пароля отправлено SMTP на %s", email)
    return sent


def issue_password_reset_token(db: Session, user: User) -> str:
    db.execute(delete(PasswordResetToken).where(PasswordResetToken.user_id == user.id))
    raw_token = generate_reset_token()
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hash_reset_token(raw_token),
            expires_at=reset_token_expiry(),
        )
    )
    db.commit()
    return raw_token


def issue_account_activation_token(db: Session, user: User) -> str:
    db.execute(delete(AccountActivationToken).where(AccountActivationToken.user_id == user.id))
    raw_token = generate_reset_token()
    db.add(
        AccountActivationToken(
            user_id=user.id,
            token_hash=hash_reset_token(raw_token),
            expires_at=activation_token_expiry(),
        )
    )
    db.commit()
    return raw_token


def consume_valid_activation_token(db: Session, raw_token: str) -> AccountActivationToken | None:
    token_hash = hash_reset_token(raw_token)
    token = db.execute(
        select(AccountActivationToken).where(AccountActivationToken.token_hash == token_hash)
    ).scalar_one_or_none()
    if token is None:
        return None
    if token.used_at is not None:
        return None
    if token.expires_at < now_utc():
        return None
    return token


def activate_user_by_token(db: Session, raw_token: str) -> User | None:
    token = consume_valid_activation_token(db=db, raw_token=raw_token)
    if token is None:
        return None
    user = db.get(User, token.user_id)
    if user is None:
        return None
    user.is_active = True
    token.used_at = now_utc()
    db.execute(delete(AccountActivationToken).where(AccountActivationToken.user_id == user.id))
    db.commit()
    return user


def _build_admin_activation_email(admin_email: str, user: User, activation_link: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = "Активация нового аккаунта"
    msg["From"] = settings.smtp_sender or "no-reply@example.com"
    msg["To"] = admin_email
    msg.set_content(
        f"Новый пользователь зарегистрировался в системе:\n\n"
        f"Имя: {user.full_name}\n"
        f"Email: {user.email}\n"
        f"ID: {user.id}\n\n"
        f"Для активации аккаунта перейдите по ссылке:\n{activation_link}\n\n"
        "Если регистрация не согласована, проигнорируйте письмо.",
        charset="utf-8",
    )
    return msg


def send_admin_activation_email(user: User, activation_link: str) -> bool:
    admin_email = settings.account_activation_admin_email or settings.smtp_sender
    if not admin_email:
        logger.warning(
            "Письмо активации не отправлено: задайте ACCOUNT_ACTIVATION_ADMIN_EMAIL "
            "или SMTP_SENDER в .env."
        )
        return False
    message = _build_admin_activation_email(
        admin_email=admin_email,
        user=user,
        activation_link=activation_link,
    )
    sent = _smtp_send_message(message)
    if sent:
        logger.info("Письмо активации нового аккаунта отправлено на %s", admin_email)
    return sent


def consume_valid_reset_token(db: Session, raw_token: str) -> PasswordResetToken | None:
    token_hash = hash_reset_token(raw_token)
    token = db.execute(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
    ).scalar_one_or_none()
    if token is None:
        return None
    if token.used_at is not None:
        return None
    if token.expires_at < now_utc():
        return None
    return token


def update_user_password(db: Session, user: User, new_password: str) -> None:
    new_salt = generate_salt()
    user.password_salt = new_salt
    user.password_hash = hash_password(password=new_password, salt=new_salt)
    db.execute(delete(PasswordResetToken).where(PasswordResetToken.user_id == user.id))
    db.commit()
