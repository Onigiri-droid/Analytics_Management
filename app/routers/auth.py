import logging
import secrets

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.templates import templates
from app.models.user import User
from app.services.auth_service import (
    activate_user_by_token,
    authenticate_user,
    is_user_pending_activation,
    consume_valid_reset_token,
    create_user,
    get_user_by_email,
    issue_password_reset_token,
    issue_account_activation_token,
    login_limiter,
    normalize_email,
    reset_limiter,
    send_admin_activation_email,
    send_password_reset_email,
    update_user_password,
    validate_password_strength,
)
from app.core.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)


def _get_or_create_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(24)
        request.session["csrf_token"] = token
    return token


def _verify_csrf(request: Request, csrf_token: str) -> bool:
    expected = request.session.get("csrf_token")
    return bool(expected and secrets.compare_digest(expected, csrf_token))


@router.get("/", response_class=HTMLResponse)
def auth_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse(url="/dashboard/", status_code=303)
    return templates.TemplateResponse(
        "auth.html",
        {
            "request": request,
            "csrf_token": _get_or_create_csrf_token(request),
            "message": None,
            "error": None,
            "active_tab": "login",
        },
    )


@router.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    if not _verify_csrf(request=request, csrf_token=csrf_token):
        return templates.TemplateResponse(
            "auth.html",
            {
                "request": request,
                "csrf_token": _get_or_create_csrf_token(request),
                "message": None,
                "error": "Ошибка безопасности формы. Обновите страницу.",
                "active_tab": "login",
            },
            status_code=400,
        )

    limiter_key = f"{request.client.host if request.client else 'unknown'}:{normalize_email(email)}"
    if not login_limiter.is_allowed(limiter_key):
        return templates.TemplateResponse(
            "auth.html",
            {
                "request": request,
                "csrf_token": _get_or_create_csrf_token(request),
                "message": None,
                "error": "Слишком много попыток входа. Повторите позже.",
                "active_tab": "login",
            },
            status_code=429,
        )

    user = authenticate_user(db=db, email=email, password=password)
    if user is None:
        pending_activation = is_user_pending_activation(db=db, email=email)
        return templates.TemplateResponse(
            "auth.html",
            {
                "request": request,
                "csrf_token": _get_or_create_csrf_token(request),
                "message": None,
                "error": (
                    "Аккаунт ещё не активирован администратором."
                    if pending_activation
                    else "Неверные email или пароль."
                ),
                "active_tab": "login",
            },
            status_code=400,
        )

    request.session.clear()
    request.session["user_id"] = user.id
    request.session["csrf_token"] = secrets.token_urlsafe(24)
    return RedirectResponse(url="/dashboard/", status_code=303)


@router.post("/register")
def register(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    password_repeat: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    if not _verify_csrf(request=request, csrf_token=csrf_token):
        return templates.TemplateResponse(
            "auth.html",
            {
                "request": request,
                "csrf_token": _get_or_create_csrf_token(request),
                "message": None,
                "error": "Ошибка безопасности формы. Обновите страницу.",
                "active_tab": "register",
            },
            status_code=400,
        )

    if password != password_repeat:
        error = "Пароли не совпадают."
    elif not validate_password_strength(password):
        error = "Пароль должен быть не короче 8 символов и содержать буквы и цифры."
    elif get_user_by_email(db=db, email=email):
        error = "Пользователь с таким email уже существует."
    else:
        error = None

    if error:
        return templates.TemplateResponse(
            "auth.html",
            {
                "request": request,
                "csrf_token": _get_or_create_csrf_token(request),
                "message": None,
                "error": error,
                "active_tab": "register",
            },
            status_code=400,
        )

    user = create_user(db=db, email=email, full_name=full_name, password=password, is_active=False)
    activation_token = issue_account_activation_token(db=db, user=user)
    activation_link = f"{settings.app_base_url.rstrip('/')}/auth/activate?token={activation_token}"
    send_admin_activation_email(user=user, activation_link=activation_link)
    return templates.TemplateResponse(
        "auth.html",
        {
            "request": request,
            "csrf_token": _get_or_create_csrf_token(request),
            "message": (
                "Аккаунт создан и ожидает активации администратором. "
                "Вы сможете войти после подтверждения."
            ),
            "error": None,
            "active_tab": "login",
        },
    )


@router.post("/forgot")
def forgot_password(
    request: Request,
    email: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    if not _verify_csrf(request=request, csrf_token=csrf_token):
        return templates.TemplateResponse(
            "auth.html",
            {
                "request": request,
                "csrf_token": _get_or_create_csrf_token(request),
                "message": None,
                "error": "Ошибка безопасности формы. Обновите страницу.",
                "active_tab": "forgot",
            },
            status_code=400,
        )

    limiter_key = f"{request.client.host if request.client else 'unknown'}:{normalize_email(email)}"
    allowed = reset_limiter.is_allowed(limiter_key)
    sent = False
    reset_link: str | None = None

    if allowed:
        user = get_user_by_email(db=db, email=email)
        if user:
            token = issue_password_reset_token(db=db, user=user)
            reset_link = f"{settings.app_base_url.rstrip('/')}/auth/reset?token={token}"
            sent = send_password_reset_email(
                email=user.email,
                full_name=user.full_name,
                reset_link=reset_link,
            )
    else:
        logger.warning(
            "Сброс пароля: превышен лимит запросов с одного IP/email за короткий интервал; "
            "письмо не отправлено."
        )

    if settings.debug:
        dbg_user = get_user_by_email(db=db, email=email)
        if not dbg_user:
            logger.warning(
                "DEBUG: сброс пароля: в базе нет пользователя с email %s — письмо не шлётся "
                "(сначала зарегистрируйтесь с этим адресом).",
                normalize_email(email),
            )
        elif not allowed:
            logger.warning("DEBUG: сброс пароля: сработал rate limit — подождите и повторите.")
        elif not sent and reset_link:
            logger.warning(
                "DEBUG: SMTP не отправил письмо; откройте ссылку сброса вручную: %s",
                reset_link,
            )
        elif sent:
            logger.info("DEBUG: письмо сброса ушло на %s (проверьте «Спам»).", dbg_user.email)

    return templates.TemplateResponse(
        "auth.html",
        {
            "request": request,
            "csrf_token": _get_or_create_csrf_token(request),
            "message": "Если адрес зарегистрирован, инструкция отправлена на почту.",
            "error": None,
            "active_tab": "forgot",
        },
    )


@router.get("/reset", response_class=HTMLResponse)
def reset_password_form(request: Request, token: str):
    return templates.TemplateResponse(
        "reset_password.html",
        {
            "request": request,
            "csrf_token": _get_or_create_csrf_token(request),
            "token": token,
            "error": None,
            "message": None,
        },
    )


@router.post("/reset", response_class=HTMLResponse)
def reset_password(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    password_repeat: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    if not _verify_csrf(request=request, csrf_token=csrf_token):
        return templates.TemplateResponse(
            "reset_password.html",
            {
                "request": request,
                "csrf_token": _get_or_create_csrf_token(request),
                "token": token,
                "error": "Ошибка безопасности формы. Обновите страницу.",
                "message": None,
            },
            status_code=400,
        )

    if password != password_repeat:
        error = "Пароли не совпадают."
    elif not validate_password_strength(password):
        error = "Пароль должен быть не короче 8 символов и содержать буквы и цифры."
    else:
        error = None

    reset_token = consume_valid_reset_token(db=db, raw_token=token)
    if reset_token is None:
        error = "Ссылка недействительна или устарела."

    if error:
        return templates.TemplateResponse(
            "reset_password.html",
            {
                "request": request,
                "csrf_token": _get_or_create_csrf_token(request),
                "token": token,
                "error": error,
                "message": None,
            },
            status_code=400,
        )

    user = db.get(User, reset_token.user_id)
    if user is None:
        return templates.TemplateResponse(
            "reset_password.html",
            {
                "request": request,
                "csrf_token": _get_or_create_csrf_token(request),
                "token": token,
                "error": "Пользователь не найден.",
                "message": None,
            },
            status_code=400,
        )

    update_user_password(db=db, user=user, new_password=password)
    return templates.TemplateResponse(
        "auth.html",
        {
            "request": request,
            "csrf_token": _get_or_create_csrf_token(request),
            "message": "Пароль обновлён. Войдите с новым паролем.",
            "error": None,
            "active_tab": "login",
        },
    )


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/auth/", status_code=303)


@router.get("/logout")
def logout_get(request: Request):
    request.session.clear()
    return RedirectResponse(url="/auth/", status_code=303)


@router.get("/activate", response_class=HTMLResponse)
def activate_account(request: Request, token: str, db: Session = Depends(get_db)):
    user = activate_user_by_token(db=db, raw_token=token)
    if user is None:
        return templates.TemplateResponse(
            "auth.html",
            {
                "request": request,
                "csrf_token": _get_or_create_csrf_token(request),
                "message": None,
                "error": "Ссылка активации недействительна или устарела.",
                "active_tab": "login",
            },
            status_code=400,
        )
    return templates.TemplateResponse(
        "auth.html",
        {
            "request": request,
            "csrf_token": _get_or_create_csrf_token(request),
            "message": f"Аккаунт {user.email} активирован. Теперь можно войти.",
            "error": None,
            "active_tab": "login",
        },
    )
