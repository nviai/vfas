"""Outgoing email over SMTP.

Email is optional. With SMTP_HOST empty it is disabled: invitation
links are only shown on screen, password reset is unavailable and
email addresses are not verified (see `verification_required`).

Messages are rendered from one template per email with three blocks
(`subject`, `text`, `html`), so their strings go through the normal
translation catalog. Sending runs in a small background pool by
default so that a slow SMTP server does not hold a web worker and the
response time does not reveal whether an account exists.
"""

from __future__ import annotations

import logging
import re
import smtplib
import ssl
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

from app.core.config import get_settings

log = logging.getLogger("app.mail")

SECURITY_MODES = ("starttls", "ssl", "none")
# Deliberately simple: one "@", no whitespace or control characters,
# a dot in the domain. Real validation is the verification email.
_EMAIL_RE = re.compile(r"[^@\s\x00-\x1f\x7f]+@[^@\s\x00-\x1f\x7f]+\.[^@\s.]+")


class MailError(Exception):
    """The message could not be handed to the SMTP server."""


@dataclass
class Mail:
    to: str
    subject: str
    text: str
    html: str | None = None


def valid_email(address: str | None) -> bool:
    value = (address or "").strip()
    return len(value) <= 200 and _EMAIL_RE.fullmatch(value) is not None


def mail_enabled() -> bool:
    return bool(get_settings().smtp_host.strip())


def verification_required() -> bool:
    """Whether users must verify their email before using a company.

    `EMAIL_VERIFICATION=auto` (default) turns it on for public sign-up
    (ALLOW_SIGNUP=true) as soon as SMTP is configured.
    """
    settings = get_settings()
    mode = (settings.email_verification or "auto").strip().lower()
    if mode == "required":
        return True
    if mode == "off":
        return False
    return settings.allow_signup and mail_enabled()


def sender() -> str:
    settings = get_settings()
    return (settings.mail_from or settings.smtp_username).strip()


# ------------------------------------------------------------ rendering


def render_mail(template: str, to: str, lang: str = "vi", **context) -> Mail:
    """Render `template` (blocks subject/text/html) into a Mail."""
    from app.core.i18n import get_translator
    from app.core.web import templates

    settings = get_settings()
    tmpl = templates.env.get_template(template)
    ctx = tmpl.new_context(
        {
            "_": get_translator(lang),
            "lang": lang,
            "product_name": settings.product_name,
            **context,
        }
    )

    def block(name: str) -> str:
        return "".join(tmpl.blocks[name](ctx)).strip()

    subject = " ".join(block("subject").split())
    return Mail(to=to, subject=subject, text=block("text"), html=block("html"))


def build_message(mail: Mail) -> EmailMessage:
    if not valid_email(mail.to):
        raise MailError("Địa chỉ email người nhận không hợp lệ")
    from_addr = sender()
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = mail.to
    msg["Subject"] = mail.subject
    msg["Date"] = formatdate(localtime=True)
    domain = parseaddr(from_addr)[1].rpartition("@")[2] or None
    msg["Message-ID"] = make_msgid(domain=domain)
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(mail.text)
    if mail.html:
        msg.add_alternative(mail.html, subtype="html")
    return msg


# ------------------------------------------------------------- delivery


def _smtp_send(msg: EmailMessage) -> None:
    settings = get_settings()
    mode = (settings.smtp_security or "starttls").strip().lower()
    if mode not in SECURITY_MODES:
        raise MailError(f"SMTP_SECURITY không hợp lệ: {mode}")
    host, port = settings.smtp_host.strip(), settings.smtp_port
    timeout = settings.smtp_timeout
    context = ssl.create_default_context()
    if mode == "ssl":
        client = smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)
    else:
        client = smtplib.SMTP(host, port, timeout=timeout)
    with client as smtp:
        smtp.ehlo()
        if mode == "starttls":
            smtp.starttls(context=context)
            smtp.ehlo()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(msg)


_transport: Callable[[EmailMessage], None] = _smtp_send
_synchronous = False
_pool: ThreadPoolExecutor | None = None


def set_transport(
    fn: Callable[[EmailMessage], None] | None, *, synchronous: bool = True
) -> None:
    """Replace the SMTP transport (tests, other delivery methods).

    `None` restores SMTP. A replaced transport runs synchronously by
    default so that tests can inspect the outbox right away.
    """
    global _transport, _synchronous
    _transport = fn or _smtp_send
    _synchronous = synchronous if fn else False


def _deliver(msg: EmailMessage) -> None:
    try:
        _transport(msg)
    except (OSError, smtplib.SMTPException, MailError) as e:
        raise MailError(str(e) or e.__class__.__name__) from e


def _deliver_logged(msg: EmailMessage) -> None:
    try:
        _deliver(msg)
    except MailError as e:
        log.error("Sending email %r failed: %s", msg["Subject"], e)


def send(mail: Mail, *, background: bool = False) -> None:
    """Send one message.

    Raises MailError when email is disabled or delivery fails (only
    when `background` is False; background failures are logged).
    """
    if not mail_enabled():
        raise MailError("Chưa cấu hình gửi email (SMTP)")
    msg = build_message(mail)
    if background and not _synchronous:
        global _pool
        if _pool is None:
            _pool = ThreadPoolExecutor(
                max_workers=2, thread_name_prefix="mail"
            )
        _pool.submit(_deliver_logged, msg)
        return
    if background:
        _deliver_logged(msg)
    else:
        _deliver(msg)
