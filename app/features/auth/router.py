"""Login, sign-up, first-run setup, company selection/switching,
invitations and language/theme preferences.
"""

from __future__ import annotations

import secrets
import threading
from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.dates import parse_date
from app.core.db import get_db
from app.core.errors import BusinessError, NotFoundError
from app.core.i18n import LANGUAGES
from app.core.registry import ROLE_LABELS
from app.core.security import (
    CurrentUser,
    create_session,
    destroy_session,
    get_current_user,
    rate_limited,
    record_event,
)
from app.core.web import (
    LANG_COOKIE,
    THEME_COOKIE,
    THEMES,
    client_ip,
    cookie_secure,
    form_data,
    redirect,
    render,
    safe_next,
)
from app.features.auth import service as auth
from app.features.auth.models import User, UserSession
from app.features.org import service as org

router = APIRouter()


def _login_response(
    request: Request, db: Session, user: User, next_url: str = "/"
) -> RedirectResponse:
    settings = get_settings()
    company_id = org.default_company_id(db, user)
    token = create_session(
        db,
        user,
        settings.session_hours,
        client_ip(request),
        company_id=company_id,
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    target = safe_next(next_url) if company_id else "/select-company"
    resp = RedirectResponse(target, status_code=303)
    resp.set_cookie(
        settings.session_cookie,
        token,
        httponly=True,
        samesite="lax",
        secure=cookie_secure(request),
        max_age=settings.session_hours * 3600,
    )
    if user.language in LANGUAGES:
        resp.set_cookie(
            LANG_COOKIE, user.language, samesite="lax", max_age=3600 * 24 * 365
        )
    return resp


# ---------------------------------------------------------------- login


@router.get("/login")
def login_page(
    request: Request, next: str = "/", db: Session = Depends(get_db)
):
    if getattr(request.state, "user", None) is not None:
        return RedirectResponse(safe_next(next), status_code=303)
    if not auth.users_exist(db):
        return RedirectResponse("/setup", status_code=303)
    return render(
        request, "auth/login.html", {"next": next, "form": {}, "error": None}
    )


@router.post("/login")
def login(
    request: Request,
    form: dict = Depends(form_data),
    db: Session = Depends(get_db),
):
    email = (form.get("email") or form.get("username") or "").strip()
    next_url = form.get("next") or "/"
    try:
        user = auth.authenticate(
            db, email, form.get("password") or "", client_ip(request)
        )
    except BusinessError as e:
        db.rollback()
        return render(
            request,
            "auth/login.html",
            {"next": next_url, "form": {"email": email}, "error": e.message},
            status_code=400,
        )
    return _login_response(request, db, user, next_url)


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    settings = get_settings()
    destroy_session(db, request.cookies.get(settings.session_cookie))
    db.commit()
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(settings.session_cookie)
    return resp


# -------------------------------------------- sign-up / first-run setup

SIGNUP_FIELDS = (
    "full_name",
    "email",
    "company_name",
    "tax_code",
    "address",
    "start_date",
)


def _signup(
    db: Session, form: dict, *, superadmin: bool
) -> tuple[User | None, dict]:
    errors: dict[str, str] = {}
    if not (form.get("full_name") or "").strip():
        errors["full_name"] = "Nhập họ tên"
    if not (form.get("company_name") or "").strip():
        errors["company_name"] = "Nhập tên công ty"
    err = org.validate_tax_code(form.get("tax_code") or "")
    if err:
        errors["tax_code"] = err
    start = None
    try:
        start = (
            parse_date(form.get("start_date"))
            if form.get("start_date")
            else None
        )
    except ValueError:
        errors["start_date"] = "Ngày không hợp lệ"
    if (form.get("password") or "") != (
        form.get("password2") or form.get("password") or ""
    ):
        errors["password2"] = "Mật khẩu nhập lại không khớp"
    if errors:
        return None, errors
    try:
        user = auth.create_user(
            db,
            form.get("email") or "",
            form.get("password") or "",
            form.get("full_name") or "",
            is_superadmin=superadmin,
            commit=False,
        )
        db.flush()
        org.create_company(
            db,
            user,
            org.CompanyInput(
                name=form.get("company_name") or "",
                tax_code=form.get("tax_code") or "",
                address=form.get("address") or "",
                start_date=start,
            ),
        )
    except BusinessError as e:
        db.rollback()
        msg = e.message
        key = (
            "email"
            if "mail" in msg.lower()
            else "password"
            if "Mật khẩu" in msg
            else "company_name"
        )
        return None, {key: msg}
    return user, {}


@router.get("/signup")
def signup_page(request: Request, db: Session = Depends(get_db)):
    if not auth.users_exist(db):
        return RedirectResponse("/setup", status_code=303)
    if not get_settings().allow_signup:
        raise NotFoundError(
            "Chức năng đăng ký đang tắt. Liên hệ quản trị để được mời."
        )
    return render(request, "auth/signup.html", {"form": {}, "errors": {}})


@router.post("/signup")
def signup(
    request: Request,
    form: dict = Depends(form_data),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    if not settings.allow_signup:
        raise NotFoundError("Chức năng đăng ký đang tắt")
    ip = client_ip(request)
    if rate_limited(
        db, "signup", settings.signup_per_ip_hour, timedelta(hours=1), ip=ip
    ):
        return render(
            request,
            "auth/signup.html",
            {
                "form": {k: form.get(k, "") for k in SIGNUP_FIELDS},
                "errors": {
                    "email": (
                        "Đăng ký quá nhiều lần từ địa chỉ này. "
                        "Vui lòng thử lại sau."
                    )
                },
            },
            status_code=429,
        )
    record_event(db, "signup", email=form.get("email"), ip=ip)
    user, errors = _signup(db, form, superadmin=False)
    if user is None:
        return render(
            request,
            "auth/signup.html",
            {
                "form": {k: form.get(k, "") for k in SIGNUP_FIELDS},
                "errors": errors,
            },
            status_code=400,
        )
    return _login_response(request, db, user)


_SETUP_LOCK = threading.Lock()


@router.get("/setup")
def setup_page(
    request: Request, token: str = "", db: Session = Depends(get_db)
):
    if auth.users_exist(db):
        return RedirectResponse("/login", status_code=303)
    return render(
        request,
        "auth/setup.html",
        {"form": {"setup_token": token}, "errors": {}},
    )


@router.post("/setup")
def setup(
    request: Request,
    form: dict = Depends(form_data),
    db: Session = Depends(get_db),
):
    """Run the first-run setup.

    Only allowed while no user exists, and only with the setup code
    (prevents takeover of a freshly deployed server).
    """
    with _SETUP_LOCK:
        if auth.users_exist(db):
            return RedirectResponse("/login", status_code=303)
        keep = {k: form.get(k, "") for k in (*SIGNUP_FIELDS, "setup_token")}
        if not auth.check_setup_token(db, form.get("setup_token")):
            record_event(db, "setup", ip=client_ip(request))
            return render(
                request,
                "auth/setup.html",
                {
                    "form": keep,
                    "errors": {
                        "setup_token": (
                            "Mã cài đặt không đúng. Xem trong log khởi động "
                            "(docker compose logs) "
                            "hoặc file .setup_token trong thư mục dữ liệu."
                        )
                    },
                },
                status_code=400,
            )
        user, errors = _signup(db, form, superadmin=True)
        if user is None:
            return render(
                request,
                "auth/setup.html",
                {"form": keep, "errors": errors},
                status_code=400,
            )
        try:
            (get_settings().data_dir / ".setup_token").unlink(missing_ok=True)
        except OSError:
            pass
        return _login_response(request, db, user)


# ------------------------------------------------------------ companies


@router.get("/select-company")
def select_company(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = org.user_companies(db, user.id)
    companies = [
        {
            "id": c.id,
            "name": c.display_name,
            "full_name": c.name,
            "tax_code": c.tax_code,
            "role_label": ROLE_LABELS.get(m.role, m.role),
            "current": c.id == user.company_id,
        }
        for c, m in rows
    ]
    return render(
        request, "auth/select_company.html", {"companies": companies}
    )


@router.post("/switch-company")
def switch_company(
    request: Request,
    form: dict = Depends(form_data),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        company_id = int(form.get("company_id") or 0)
    except ValueError:
        company_id = 0
    ids = {c.id for c, _ in org.user_companies(db, user.id)}
    if company_id not in ids:
        raise NotFoundError("Bạn không phải thành viên của công ty này")
    sess = db.get(UserSession, user.session_id) if user.session_id else None
    if sess is not None:
        sess.company_id = company_id
        # Rotate the CSRF token so forms/tabs still open for the old
        # company cannot be submitted to the new one by mistake.
        sess.csrf_token = secrets.token_urlsafe(32)
    u = db.get(User, user.id)
    u.last_company_id = company_id
    db.commit()
    return redirect("/", request=request)


# ---------------------------------------------------------- invitations


@router.get("/invite/{token}")
def invite_page(request: Request, token: str, db: Session = Depends(get_db)):
    inv = org.find_invitation(db, token)
    has_account = auth.get_user_by_email(db, inv.email) is not None
    ctx = {
        "invitation": {
            "email": inv.email,
            "company_name": inv.company.name,
            "role_label": ROLE_LABELS.get(inv.role, inv.role),
        },
        "has_account": has_account,
        "errors": {},
        "token": token,
    }
    return render(request, "auth/invite.html", ctx)


@router.post("/invite/{token}")
def invite_accept(
    request: Request,
    token: str,
    form: dict = Depends(form_data),
    db: Session = Depends(get_db),
):
    inv = org.find_invitation(db, token)
    user = auth.get_user_by_email(db, inv.email)
    current: CurrentUser | None = getattr(request.state, "user", None)
    if user is None:
        try:
            user = auth.create_user(
                db,
                inv.email,
                form.get("password") or "",
                form.get("full_name") or "",
                commit=False,
            )
            db.flush()
        except BusinessError as e:
            db.rollback()
            ctx = {
                "invitation": {
                    "email": inv.email,
                    "company_name": inv.company.name,
                    "role_label": ROLE_LABELS.get(inv.role, inv.role),
                },
                "has_account": False,
                "errors": {"password": e.message},
                "token": token,
            }
            return render(request, "auth/invite.html", ctx, status_code=400)
    elif current is None or current.id != user.id:
        # The account already exists: the user must log in as that
        # account before accepting the invitation.
        return redirect(
            f"/login?next=/invite/{token}",
            "Đăng nhập bằng email được mời để nhận lời mời",
            "info",
        )
    org.accept_invitation(db, inv, user)
    if current is not None and current.id == user.id:
        sess = (
            db.get(UserSession, current.session_id)
            if current.session_id
            else None
        )
        if sess is not None:
            sess.company_id = inv.company_id
            db.commit()
        return redirect("/", f"Đã tham gia {inv.company.name}")
    return _login_response(request, db, user)


# ------------------------------------------------------ language, theme


@router.get("/lang/{code}")
def set_language(
    request: Request,
    code: str,
    next: str | None = None,
    db: Session = Depends(get_db),
):
    if code not in LANGUAGES:
        raise NotFoundError()
    back = safe_next(
        next
        or request.headers.get("referer", "/").split(request.url.netloc, 1)[
            -1
        ],
        "/",
    )
    resp = RedirectResponse(back, status_code=303)
    resp.set_cookie(LANG_COOKIE, code, samesite="lax", max_age=3600 * 24 * 365)
    user = getattr(request.state, "user", None)
    if user is not None:
        u = db.get(User, user.id)
        u.language = code
        db.commit()
    return resp


@router.get("/theme/{name}")
def set_theme(request: Request, name: str, db: Session = Depends(get_db)):
    if name not in THEMES:
        raise NotFoundError()
    back = safe_next(
        request.headers.get("referer", "/").split(request.url.netloc, 1)[-1],
        "/",
    )
    resp = RedirectResponse(back, status_code=303)
    resp.set_cookie(
        THEME_COOKIE, name, samesite="lax", max_age=3600 * 24 * 365
    )
    user = getattr(request.state, "user", None)
    if user is not None:
        u = db.get(User, user.id)
        u.theme = name
        db.commit()
    return resp
