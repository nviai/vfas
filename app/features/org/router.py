"""Companies: creation, profile & settings, members, "my account" and
platform administration.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import mail
from app.core.config import get_settings
from app.core.dates import parse_date
from app.core.db import get_db
from app.core.errors import BusinessError, NotFoundError, PermissionDeniedError
from app.core.regimes import REGIMES
from app.core.registry import ROLE_LABELS, ROLES
from app.core.security import (
    CurrentUser,
    get_current_user,
    rate_limited,
    record_event,
    require,
    require_superadmin,
    require_verified_email,
)
from app.core.web import (
    client_ip,
    cookie_secure,
    form_data,
    get_ctx,
    get_tenant_db,
    public_base_url,
    redirect,
    render,
    resolve_lang,
)
from app.features.auth import service as auth
from app.features.auth.models import User, UserSession
from app.features.journal.models import JournalEntry
from app.features.org import service as org
from app.features.org.models import Company, Membership

router = APIRouter()


# ----------------------------------------------------- company creation


def _can_create_company(user: CurrentUser) -> bool:
    require_verified_email(user)
    return user.is_superadmin or get_settings().allow_signup


@router.get("/companies/new")
def company_new(
    request: Request, user: CurrentUser = Depends(get_current_user)
):
    if not _can_create_company(user):
        raise PermissionDeniedError(
            "Chỉ quản trị hệ thống được tạo công ty mới"
        )
    return render(
        request,
        "org/company_new.html",
        {"form": {}, "errors": {}, "regimes": list(REGIMES.values())},
    )


@router.post("/companies/new")
def company_create(
    request: Request,
    form: dict = Depends(form_data),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not _can_create_company(user):
        raise PermissionDeniedError(
            "Chỉ quản trị hệ thống được tạo công ty mới"
        )
    settings = get_settings()
    if not user.is_superadmin:
        if (
            settings.max_companies_per_user
            and db.scalar(
                select(func.count(Company.id)).where(
                    Company.created_by == user.id
                )
            )
            >= settings.max_companies_per_user
        ):
            raise PermissionDeniedError(
                f"Mỗi tài khoản chỉ được tạo tối đa "
                f"{settings.max_companies_per_user} công ty"
            )
        if rate_limited(
            db, "company", 10, timedelta(hours=1), email=user.email
        ):
            raise PermissionDeniedError(
                "Tạo công ty quá nhiều lần. Vui lòng thử lại sau."
            )
        record_event(db, "company", email=user.email, ip=client_ip(request))
    errors = {}
    start = None
    try:
        start = (
            parse_date(form.get("start_date"))
            if form.get("start_date")
            else None
        )
    except ValueError:
        errors["start_date"] = "Ngày không hợp lệ"
    if not errors:
        try:
            owner = db.get(User, user.id)
            c = org.create_company(
                db,
                owner,
                org.CompanyInput(
                    name=form.get("name") or "",
                    tax_code=form.get("tax_code") or "",
                    address=form.get("address") or "",
                    start_date=start,
                    vat_period=form.get("vat_period") or "quarter",
                    business_type=form.get("business_type") or "service",
                    regime=form.get("regime") or "tt133",
                ),
            )
        except BusinessError as e:
            db.rollback()
            errors["tax_code" if "thuế" in e.message else "name"] = e.message
    if errors:
        return render(
            request,
            "org/company_new.html",
            {
                "form": form,
                "errors": errors,
                "regimes": list(REGIMES.values()),
            },
            status_code=400,
        )
    sess = db.get(UserSession, user.session_id) if user.session_id else None
    if sess is not None:
        sess.company_id = c.id
    db.commit()
    return redirect("/", f"Đã tạo công ty {c.name}")


# ------------------------------------------------------ company profile


def _has_entries(db: Session, company_id: int) -> bool:
    return bool(
        db.scalar(
            select(func.count(JournalEntry.id)).where(
                JournalEntry.company_id == company_id,
                JournalEntry.status != "draft",
            )
        )
    )


@router.get("/settings")
def settings_page(
    request: Request,
    tab: str = "general",
    user: CurrentUser = Depends(require("settings.manage")),
    db: Session = Depends(get_tenant_db),
):
    c = db.get(Company, user.company_id)
    return render(
        request,
        "org/settings.html",
        {
            "c": c,
            "tab": tab,
            "errors": {},
            "can_change_start": not _has_entries(db, c.id),
            "regimes": list(REGIMES.values()),
            "regime_locked": org.regime_locked(db, c.id),
            "prefixes": c.setting("prefixes") or {},
            "breadcrumbs": [("Cài đặt", None)],
        },
    )


@router.post("/settings")
def settings_save(
    request: Request,
    form: dict = Depends(form_data),
    user: CurrentUser = Depends(require("settings.manage")),
    db: Session = Depends(get_tenant_db),
):
    ctx = get_ctx(request)
    tab = form.get("tab") or "general"
    c = db.get(Company, ctx.company_id)
    try:
        data = {k: form[k] for k in org.COMPANY_FIELDS if k in form}
        org.update_company(db, ctx, data)
        if form.get("start_date"):
            start = parse_date(form["start_date"])
            if start != c.start_date:
                org.set_start_date(db, ctx, start)
        if form.get("regime") and form["regime"] != c.regime:
            org.set_regime(db, ctx, form["regime"])
        prefixes = {
            k[7:]: v.strip().upper()
            for k, v in form.items()
            if k.startswith("prefix_") and v.strip()
        }
        if prefixes:
            org.update_company_settings(db, ctx, "prefixes", prefixes)
    except (BusinessError, ValueError) as e:
        db.rollback()
        msg = (
            e.message if isinstance(e, BusinessError) else "Ngày không hợp lệ"
        )
        return redirect(f"/settings?tab={tab}", msg, "error")
    return redirect(f"/settings?tab={tab}", "Đã lưu thông tin công ty")


# -------------------------------------------------------------- members

INVITE_COOKIE = "vfas_invite_link"


def _invite_serializer():
    from itsdangerous import URLSafeTimedSerializer

    from app.core.config import get_settings as _gs

    return URLSafeTimedSerializer(_gs().resolved_secret(), salt="invite-link")


@router.get("/members")
def members_page(
    request: Request,
    user: CurrentUser = Depends(require("members.manage")),
    db: Session = Depends(get_tenant_db),
):
    rows = org.members(db, user.company_id)
    new_link = None
    raw = request.cookies.get(INVITE_COOKIE)
    if raw:
        try:
            new_link = _invite_serializer().loads(raw, max_age=600)
        except Exception:  # noqa: BLE001 - broken/expired cookie
            new_link = None
    resp = render(
        request,
        "org/members.html",
        {
            "members": [
                {
                    "id": m.id,
                    "user_id": u.id,
                    "email": u.email,
                    "full_name": u.full_name,
                    "role": m.role,
                    "role_label": ROLE_LABELS.get(m.role, m.role),
                    "active": m.is_active and u.is_active,
                    "is_me": u.id == user.id,
                    "last_login": u.last_login_at,
                }
                for m, u in rows
            ],
            "invitations": org.pending_invitations(db, user.company_id),
            "roles": [(r, ROLE_LABELS[r]) for r in ROLES],
            "new_link": new_link,
            "breadcrumbs": [("Thành viên", None)],
        },
    )
    if raw:
        resp.delete_cookie(INVITE_COOKIE, path="/members")
    return resp


def _send_invitation(request: Request, inv, link: str) -> Response:
    """Email the invitation link (when SMTP is configured).

    The link is also shown once on the members page as a fallback.
    """
    sent, error = False, None
    if mail.mail_enabled():
        user = request.state.user
        try:
            mail.send(
                mail.render_mail(
                    "org/email/invite.html",
                    inv.email,
                    resolve_lang(request),
                    inviter=user.display_name,
                    company_name=inv.company.name,
                    role_label=ROLE_LABELS.get(inv.role, inv.role),
                    link=link,
                    days=org.INVITE_DAYS,
                )
            )
            sent = True
        except mail.MailError as e:
            error = str(e)
    if sent:
        message, level = (
            f"Đã gửi email mời tới {inv.email}. "
            "Đường link bên dưới dùng khi họ không nhận được email.",
            "success",
        )
    elif error:
        message, level = (
            f"Không gửi được email mời ({error}). "
            f"Gửi đường link bên dưới cho {inv.email}.",
            "warning",
        )
    else:
        message, level = (
            f"Đã tạo lời mời cho {inv.email}. Gửi đường link bên dưới cho họ.",
            "success",
        )
    # Keep the link (it contains the token) out of URLs/logs: store
    # it in a signed cookie that the members page reads once.
    resp = redirect("/members", message, level, request=request)
    resp.set_cookie(
        INVITE_COOKIE,
        _invite_serializer().dumps(link),
        max_age=600,
        httponly=True,
        samesite="strict",
        secure=cookie_secure(request),
        path="/members",
    )
    return resp


@router.post("/members/invite")
def members_invite(
    request: Request,
    form: dict = Depends(form_data),
    _u: CurrentUser = Depends(require("members.manage")),
    db: Session = Depends(get_tenant_db),
):
    ctx = get_ctx(request)
    inv, token = org.invite(
        db, ctx, form.get("email") or "", form.get("role") or ""
    )
    return _send_invitation(
        request, inv, f"{public_base_url(request)}/invite/{token}"
    )


@router.post("/invitations/{invitation_id}/resend")
def invitation_resend(
    request: Request,
    invitation_id: int,
    _u: CurrentUser = Depends(require("members.manage")),
    db: Session = Depends(get_tenant_db),
):
    inv, token = org.renew_invitation(db, get_ctx(request), invitation_id)
    return _send_invitation(
        request, inv, f"{public_base_url(request)}/invite/{token}"
    )


@router.post("/members/{membership_id}/role")
def members_role(
    request: Request,
    membership_id: int,
    form: dict = Depends(form_data),
    _u: CurrentUser = Depends(require("members.manage")),
    db: Session = Depends(get_tenant_db),
):
    org.change_role(
        db, get_ctx(request), membership_id, form.get("role") or ""
    )
    return redirect("/members", "Đã đổi vai trò", request=request)


@router.post("/members/{membership_id}/toggle")
def members_toggle(
    request: Request,
    membership_id: int,
    form: dict = Depends(form_data),
    _u: CurrentUser = Depends(require("members.manage")),
    db: Session = Depends(get_tenant_db),
):
    active = form.get("active") == "1"
    org.set_member_active(db, get_ctx(request), membership_id, active)
    return redirect(
        "/members",
        "Đã mở lại quyền truy cập" if active else "Đã khóa quyền truy cập",
        request=request,
    )


@router.post("/invitations/{invitation_id}/revoke")
def invitation_revoke(
    request: Request,
    invitation_id: int,
    _u: CurrentUser = Depends(require("members.manage")),
    db: Session = Depends(get_tenant_db),
):
    org.revoke_invitation(db, get_ctx(request), invitation_id)
    return redirect("/members", "Đã thu hồi lời mời", request=request)


# ----------------------------------------------------------- my account


@router.get("/me")
def me_page(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    u = db.get(User, user.id)
    mine = [
        {
            "name": c.display_name,
            "role_label": ROLE_LABELS.get(m.role, m.role),
            "id": c.id,
        }
        for c, m in org.user_companies(db, user.id)
    ]
    now = datetime.now(timezone.utc)
    sessions = [
        {
            "current": x.id == user.session_id,
            "ip": x.ip,
            "ua": x.user_agent or "",
            "created": x.created_at,
            "seen": x.last_seen_at or x.created_at,
        }
        for x in db.scalars(
            select(UserSession)
            .where(UserSession.user_id == user.id)
            .order_by(UserSession.created_at.desc())
        )
        if (
            x.expires_at
            if x.expires_at.tzinfo
            else x.expires_at.replace(tzinfo=timezone.utc)
        )
        > now
    ]
    return render(
        request,
        "org/me.html",
        {
            "u": u,
            "my_companies": mine,
            "errors": {},
            "sessions": sessions,
            "breadcrumbs": [("Tài khoản của tôi", None)],
        },
    )


@router.post("/me")
def me_save(
    request: Request,
    form: dict = Depends(form_data),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    u = db.get(User, user.id)
    auth.update_profile(
        db,
        u,
        full_name=form.get("full_name"),
        language=form.get("language"),
        theme=form.get("theme"),
    )
    resp = redirect("/me", "Đã lưu")
    if form.get("language") in ("vi", "en"):
        resp.set_cookie(
            "lang", form["language"], samesite="lax", max_age=3600 * 24 * 365
        )
    if form.get("theme") in ("light", "dark", "system"):
        resp.set_cookie(
            "theme", form["theme"], samesite="lax", max_age=3600 * 24 * 365
        )
    return resp


@router.post("/me/password")
def me_password(
    request: Request,
    form: dict = Depends(form_data),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    u = db.get(User, user.id)
    if (form.get("new_password") or "") != (form.get("new_password2") or ""):
        return redirect("/me", "Mật khẩu nhập lại không khớp", "error")
    auth.change_password(
        db,
        u,
        form.get("current_password") or "",
        form.get("new_password") or "",
        keep_session=user.session_id,
        ip=client_ip(request),
    )
    return redirect(
        "/me", "Đã đổi mật khẩu. Các phiên đăng nhập khác đã bị đăng xuất."
    )


@router.post("/me/sessions/logout-others")
def me_logout_others(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.core.security import destroy_user_sessions

    destroy_user_sessions(db, user.id, except_id=user.session_id)
    db.commit()
    return redirect("/me", "Đã đăng xuất mọi phiên khác")


# ---------------------------------------------- platform administration


@router.get("/admin")
def admin_page(
    request: Request,
    _u: CurrentUser = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    member_count = dict(
        db.execute(
            select(Membership.company_id, func.count(Membership.id)).group_by(
                Membership.company_id
            )
        ).all()
    )
    entry_count = dict(
        db.execute(
            select(
                JournalEntry.company_id, func.count(JournalEntry.id)
            ).group_by(JournalEntry.company_id)
        ).all()
    )
    rows = [
        {
            "c": c,
            "members": member_count.get(c.id, 0),
            "entries": entry_count.get(c.id, 0),
        }
        for c in db.scalars(
            select(Company).order_by(Company.created_at.desc())
        )
    ]
    users = list(
        db.scalars(select(User).order_by(User.created_at.desc()).limit(200))
    )
    return render(
        request,
        "org/admin.html",
        {
            "company_rows": rows,
            "all_users": users,
            "breadcrumbs": [("Quản trị hệ thống", None)],
        },
    )


@router.post("/admin/companies/{company_id}/toggle")
def admin_company_toggle(
    request: Request,
    company_id: int,
    user: CurrentUser = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    from app.core.audit import write_audit

    c = db.get(Company, company_id)
    if c is None:
        raise NotFoundError()
    c.is_active = not c.is_active
    write_audit(
        db,
        None,
        action="admin.company_toggle",
        entity_type="company",
        entity_id=c.id,
        company_id=c.id,
        user=user,
        summary=("Mở" if c.is_active else "Khóa") + f" công ty {c.name}",
    )
    db.commit()
    return redirect("/admin", "Đã cập nhật", request=request)


@router.post("/admin/users/{user_id}/toggle")
def admin_user_toggle(
    request: Request,
    user_id: int,
    user: CurrentUser = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    from app.core.audit import write_audit
    from app.core.security import destroy_user_sessions

    u = db.get(User, user_id)
    if u is None:
        raise NotFoundError()
    if u.id == user.id:
        raise BusinessError("Không thể tự khóa chính mình")
    u.is_active = not u.is_active
    if not u.is_active:
        destroy_user_sessions(db, u.id)
    write_audit(
        db,
        None,
        action="admin.user_toggle",
        entity_type="user",
        entity_id=u.id,
        user=user,
        summary=("Mở" if u.is_active else "Khóa") + f" tài khoản {u.email}",
    )
    db.commit()
    return redirect("/admin", "Đã cập nhật", request=request)
