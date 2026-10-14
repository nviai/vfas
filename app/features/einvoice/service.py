"""Issue e-invoices for sales documents through a provider.

Flow (see docs/EINVOICE.md):

1. A draft sales document (BH) – or a draft return/allowance (TL) of a
   sale whose invoice was issued here – is turned into a
   CanonicalInvoice dated today (invoices are never back-dated).
2. An outbox row with a fresh idempotency key is committed BEFORE the
   provider is called.
3. The answer is stored. A lost answer leaves the row "unknown"; it is
   looked up by key before anything is sent again.
4. The invoice template/series/number/date are written to the sales
   document and its draft VAT lines, then the voucher is posted (when
   the company chose "post after issuing").

Issued invoices lock the document: it cannot be edited, deleted or
cancelled any more – corrections go through an adjustment invoice.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import crypto
from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.dates import today
from app.core.errors import BusinessError, NotFoundError
from app.core.money import amount_in_words
from app.core.netsafety import public_https_error
from app.features.einvoice import providers
from app.features.einvoice.models import EInvoiceConfig, EInvoiceIssue
from app.features.einvoice.providers.base import (
    ACTIVE_STATES,
    FAILED,
    ISSUED,
    PENDING,
    SIGNED,
    UNKNOWN,
    CanonicalInvoice,
    EInvoiceProvider,
    InvoiceLine,
    IssueResult,
    OriginalInvoice,
    ProviderConfig,
    ProviderError,
    ProviderRejectedError,
    ProviderUnavailableError,
)
from app.features.journal import service as js
from app.features.journal.models import JournalEntry, VatRecord
from app.features.org.models import Company
from app.features.sales.models import RETURN, SALE, SalesDoc

ENVIRONMENTS = {"test": "Thử nghiệm", "production": "Chính thức"}
STATUS_LABELS = {
    PENDING: "Đang gửi",
    UNKNOWN: "Chưa rõ kết quả",
    SIGNED: "Đã ký, chờ mã CQT",
    ISSUED: "Đã cấp mã",
    FAILED: "Lỗi",
}
# Series: C/K (with/without tax authority code), 2-digit year, invoice
# type letter, 2 letters chosen by the seller (Circular 91/2026).
SERIES_RE = re.compile(r"[CK]\d{2}[TDLMNBGH][A-Z]{2}")
TEMPLATES = ("1", "2")
PAYMENT_LABELS = {"cash": "TM", "bank": "CK", "credit": "TM/CK"}
# Polling backoff after each check of a "signed"/"unknown" invoice.
BACKOFF = (
    timedelta(seconds=15),
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=15),
    timedelta(hours=1),
    timedelta(hours=4),
)
GIVE_UP_AFTER = timedelta(days=3)
# Another issue for the same series sent less than this long ago and
# still pending blocks a new one (providers number per series).
SERIES_GUARD = timedelta(minutes=2)

_client: httpx.Client | None = None


def set_http_client(client: httpx.Client | None) -> None:
    """Replace the HTTP client used by adapters (tests)."""
    global _client
    _client = client


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(d: datetime | None) -> datetime | None:
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# ------------------------------------------------------- configuration


def get_config(db: Session, company_id: int) -> EInvoiceConfig | None:
    return db.scalar(
        select(EInvoiceConfig).where(EInvoiceConfig.company_id == company_id)
    )


def config_secrets(cfg: EInvoiceConfig | None) -> dict:
    if cfg is None or not cfg.secrets:
        return {}
    raw = crypto.decrypt(cfg.secrets)
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


def default_template(company: Company) -> str:
    return "2" if company.vat_method == "truc_tiep" else "1"


def save_config(db: Session, ctx, form: dict) -> EInvoiceConfig:
    """Save the provider settings.

    Provider fields arrive as "<provider>__<field>"; secret fields left
    empty keep their stored value.
    """
    code = (form.get("provider") or "").strip()
    cls = providers.provider_class(code)
    if cls is None:
        raise BusinessError("Chọn nhà cung cấp hóa đơn điện tử")
    environment = form.get("environment") or "test"
    if environment not in ENVIRONMENTS:
        raise BusinessError("Môi trường không hợp lệ")
    template = (form.get("template") or "1").strip()
    if template not in TEMPLATES:
        raise BusinessError("Mẫu số hóa đơn là 1 (GTGT) hoặc 2 (bán hàng)")
    series = (form.get("series") or "").strip().upper()
    if not SERIES_RE.fullmatch(series):
        raise BusinessError(
            "Ký hiệu hóa đơn gồm 6 ký tự, ví dụ C26TAA "
            "(C/K, 2 số cuối của năm, loại hóa đơn, 2 chữ cái)"
        )
    base_url = (form.get("base_url") or "").strip().rstrip("/")
    if base_url:
        err = public_https_error(base_url)
        if err:
            raise BusinessError(f"Địa chỉ API không dùng được: {err}")
    cfg = get_config(db, ctx.company_id)
    if cfg is None:
        cfg = EInvoiceConfig(company_id=ctx.company_id, settings={})
        db.add(cfg)
    secrets_ = config_secrets(cfg) if cfg.provider == code else {}
    settings: dict = {}
    for f in cls.fields:
        # Inputs are prefixed with the provider code on the form.
        value = (form.get(f"{code}__{f.name}") or "").strip()
        if f.secret:
            if value:
                secrets_[f.name] = value
            if f.required and not secrets_.get(f.name):
                raise BusinessError(f"Nhập {f.label}")
        else:
            if f.required and not value:
                raise BusinessError(f"Nhập {f.label}")
            settings[f.name] = value
    before = {
        "provider": cfg.provider,
        "environment": cfg.environment,
        "series": cfg.series,
        "is_active": cfg.is_active,
    }
    cfg.provider = code
    cfg.environment = environment
    cfg.base_url = base_url
    cfg.settings = settings
    cfg.secrets = crypto.encrypt(json.dumps(secrets_)) if secrets_ else None
    cfg.template = template
    cfg.series = series
    cfg.auto_post = form.get("auto_post") in ("1", "on", "true")
    cfg.is_active = form.get("is_active") in ("1", "on", "true")
    cfg.updated_at = _now()
    cfg.updated_by = ctx.user_id
    write_audit(
        db,
        ctx,
        action="einvoice.config",
        entity_type="einvoice_config",
        entity_id=ctx.company_id,
        summary=f"Cấu hình hóa đơn điện tử ({cls.name}, {series})",
        before=before,
        after={
            "provider": code,
            "environment": environment,
            "series": series,
            "is_active": cfg.is_active,
        },
    )
    db.commit()
    return cfg


def provider_for(
    company: Company, cfg: EInvoiceConfig, *, environment: str | None = None
) -> EInvoiceProvider:
    cls = providers.provider_class(cfg.provider)
    if cls is None:
        raise BusinessError("Nhà cung cấp hóa đơn điện tử không còn hỗ trợ")
    if cfg.base_url:
        # Checked again at call time: DNS may have changed since saving.
        err = public_https_error(cfg.base_url)
        if err:
            raise BusinessError(f"Địa chỉ API không dùng được: {err}")
    values = dict(cfg.settings or {}) | config_secrets(cfg)
    pc = ProviderConfig(
        environment=environment or cfg.environment,
        base_url=cfg.base_url,
        tax_code=company.tax_code,
        template=cfg.template,
        series=cfg.series,
        values=values,
    )
    return cls(pc, _client)


def test_connection(db: Session, ctx) -> str:
    cfg = get_config(db, ctx.company_id)
    if cfg is None or not cfg.provider:
        raise BusinessError("Chưa cấu hình nhà cung cấp hóa đơn điện tử")
    company = db.get(Company, ctx.company_id)
    try:
        return provider_for(company, cfg).test_connection()
    except ProviderError as e:
        raise BusinessError(f"Kết nối thất bại: {e.message}") from e


# ------------------------------------------------------------- queries


def issues_for_doc(
    db: Session, company_id: int, doc_id: int
) -> list[EInvoiceIssue]:
    return list(
        db.scalars(
            select(EInvoiceIssue)
            .where(
                EInvoiceIssue.company_id == company_id,
                EInvoiceIssue.sales_doc_id == doc_id,
            )
            .order_by(EInvoiceIssue.id.desc())
        )
    )


def active_issue(
    db: Session, company_id: int, doc_id: int
) -> EInvoiceIssue | None:
    for issue in issues_for_doc(db, company_id, doc_id):
        if issue.status in ACTIVE_STATES:
            return issue
    return None


def get_issue(db: Session, company_id: int, issue_id: int) -> EInvoiceIssue:
    issue = db.get(EInvoiceIssue, issue_id)
    if issue is None or issue.company_id != company_id:
        raise NotFoundError("Không tìm thấy hóa đơn điện tử")
    return issue


def doc_for_entry(db: Session, company_id: int, entry_id: int) -> SalesDoc:
    doc = db.scalar(
        select(SalesDoc).where(
            SalesDoc.company_id == company_id, SalesDoc.entry_id == entry_id
        )
    )
    if doc is None:
        raise NotFoundError("Không tìm thấy chứng từ bán hàng")
    return doc


def list_issues(
    db: Session, company_id: int, *, status: str = "", limit: int = 200
) -> list[EInvoiceIssue]:
    q = select(EInvoiceIssue).where(EInvoiceIssue.company_id == company_id)
    if status:
        q = q.where(EInvoiceIssue.status == status)
    return list(db.scalars(q.order_by(EInvoiceIssue.id.desc()).limit(limit)))


# ---------------------------------------------------- canonical invoice


def original_issue(db: Session, doc: SalesDoc) -> EInvoiceIssue | None:
    if doc.kind != RETURN or not doc.original_entry_id:
        return None
    original = db.scalar(
        select(SalesDoc).where(
            SalesDoc.company_id == doc.company_id,
            SalesDoc.entry_id == doc.original_entry_id,
        )
    )
    if original is None:
        return None
    issue = active_issue(db, doc.company_id, original.id)
    return issue if issue and issue.status in (SIGNED, ISSUED) else None


def build_invoice(
    db: Session,
    company: Company,
    cfg: EInvoiceConfig,
    doc: SalesDoc,
    key: str,
) -> CanonicalInvoice:
    lines = [
        InvoiceLine(
            line_no=ln.line_no,
            name=ln.description,
            unit=ln.unit or "",
            code=ln.item.code if ln.item else "",
            quantity=ln.quantity,
            unit_price=ln.unit_price,
            amount=ln.amount,
            discount=ln.discount_amount,
            vat_rate=ln.vat_rate,
            vat_amount=ln.vat_amount,
        )
        for ln in doc.lines
    ]
    partner = doc.partner
    inv = CanonicalInvoice(
        key=key,
        seller_tax_code=company.tax_code,
        template=cfg.template,
        series=cfg.series,
        issue_date=today(),
        lines=lines,
        buyer_legal_name=doc.partner_name,
        buyer_name=doc.buyer_name,
        buyer_tax_code=doc.buyer_tax_code,
        buyer_address=doc.buyer_address,
        buyer_email=(partner.email if partner else "") or "",
        buyer_code=(partner.code if partner else "") or "",
        payment_method=PAYMENT_LABELS.get(doc.payment_method, "TM/CK"),
        note=doc.note or "",
    )
    inv.amount_in_words = amount_in_words(inv.total)
    if doc.kind == RETURN:
        orig = original_issue(db, doc)
        if orig is None:
            raise BusinessError(
                "Chứng từ giảm giá/trả lại chỉ lập hóa đơn điều chỉnh khi "
                "hóa đơn gốc đã được phát hành qua phần mềm"
            )
        inv.kind = "adjust_down"
        inv.original = OriginalInvoice(
            template=orig.template,
            series=orig.series,
            number=orig.number,
            issue_date=orig.issue_date,
            provider_ref=orig.provider_ref,
            tax_authority_code=orig.tax_authority_code,
        )
        inv.reason = doc.note or "Điều chỉnh giảm do trả lại/giảm giá hàng bán"
    return inv


def invoice_from_issue(issue: EInvoiceIssue) -> CanonicalInvoice:
    """Rebuild the canonical invoice that was sent (for lookups)."""
    from datetime import date as _date
    from decimal import Decimal

    data = dict(issue.request or {})
    lines = []
    for ln in data.pop("lines", []):
        for f in ("quantity", "unit_price"):
            if ln.get(f) is not None:
                ln[f] = Decimal(ln[f])
        lines.append(InvoiceLine(**ln))
    original = data.pop("original", None)
    if original:
        original["issue_date"] = (
            _date.fromisoformat(original["issue_date"])
            if original.get("issue_date")
            else None
        )
        original = OriginalInvoice(**original)
    data["issue_date"] = _date.fromisoformat(data["issue_date"])
    return CanonicalInvoice(lines=lines, original=original, **data)


def _result_of(issue: EInvoiceIssue) -> IssueResult:
    return IssueResult(
        status=issue.status,
        number=issue.number,
        template=issue.template,
        series=issue.series,
        issue_date=issue.issue_date,
        provider_ref=issue.provider_ref,
        lookup_code=issue.lookup_code,
        tax_authority_code=issue.tax_authority_code,
    )


# ---------------------------------------------------------------- issue


def _check_doc(db: Session, ctx, doc: SalesDoc) -> JournalEntry:
    entry = db.get(JournalEntry, doc.entry_id)
    if entry is None or entry.company_id != ctx.company_id:
        raise NotFoundError("Không tìm thấy chứng từ")
    if not entry.is_draft:
        raise BusinessError(
            "Chỉ phát hành hóa đơn cho chứng từ nháp (chưa ghi sổ): số hóa "
            "đơn phải có trên chứng từ và bảng kê thuế trước khi ghi sổ. "
            "Hủy chứng từ (kỳ đang mở) rồi lập lại nếu cần."
        )
    if doc.kind not in (SALE, RETURN):
        raise BusinessError("Loại chứng từ không lập được hóa đơn")
    if doc.einvoice_key or (
        doc.invoice_no and not active_issue(db, ctx.company_id, doc.id)
    ):
        raise BusinessError(
            "Chứng từ đã có số hóa đơn (nhập từ hóa đơn có sẵn) – "
            "không phát hành lại"
        )
    if not doc.lines or not any(ln.amount for ln in doc.lines):
        raise BusinessError("Chứng từ chưa có hàng hóa, dịch vụ")
    if entry.date > today():
        raise BusinessError(
            "Không phát hành hóa đơn cho chứng từ ngày tương lai"
        )
    return entry


def _apply_result(
    db: Session, issue: EInvoiceIssue, result: IssueResult
) -> None:
    issue.status = result.status
    issue.number = result.number or issue.number
    issue.template = result.template or issue.template
    issue.series = result.series or issue.series
    issue.issue_date = result.issue_date or issue.issue_date
    issue.provider_ref = result.provider_ref or issue.provider_ref
    issue.lookup_code = result.lookup_code or issue.lookup_code
    issue.tax_authority_code = (
        result.tax_authority_code or issue.tax_authority_code
    )
    issue.response = _trim(result.raw)
    issue.error = ""
    issue.updated_at = _now()
    issue.next_check_at = (
        _now() + BACKOFF[0] if issue.status == SIGNED else None
    )


def _trim(raw) -> dict:
    text = json.dumps(raw, ensure_ascii=False, default=str)
    if len(text) > 20_000:
        return {"truncated": text[:20_000]}
    return raw if isinstance(raw, dict) else {"raw": raw}


def _write_back(db: Session, issue: EInvoiceIssue, doc: SalesDoc) -> None:
    """Copy the invoice number to the document and its VAT lines."""
    if not issue.number:
        return
    company = db.get(Company, doc.company_id)
    doc.invoice_template = issue.template
    doc.invoice_series = issue.series
    doc.invoice_no = issue.number
    doc.invoice_date = issue.issue_date
    doc.einvoice_key = (
        f"{company.tax_code}|{issue.template}{issue.series}"
        f"|{issue.number.lstrip('0')}"
    )
    entry = db.get(JournalEntry, doc.entry_id)
    if entry is not None and entry.is_draft:
        ref = f"{issue.series}-{issue.number}"
        entry.ref_no = ref
        for v in db.scalars(
            select(VatRecord).where(VatRecord.entry_id == entry.id)
        ):
            v.invoice_template = issue.template
            v.invoice_series = issue.series
            v.invoice_no = issue.number
            v.invoice_date = issue.issue_date


def _clear_write_back(db: Session, doc: SalesDoc) -> None:
    """Remove a refused invoice number from a draft document."""
    entry = db.get(JournalEntry, doc.entry_id)
    if entry is None or not entry.is_draft:
        return
    doc.invoice_template = doc.invoice_series = doc.invoice_no = ""
    doc.invoice_date = None
    doc.einvoice_key = None
    entry.ref_no = ""
    for v in db.scalars(
        select(VatRecord).where(VatRecord.entry_id == entry.id)
    ):
        v.invoice_template = v.invoice_series = v.invoice_no = ""


def issue_for_doc(db: Session, ctx, doc: SalesDoc) -> EInvoiceIssue:
    """Issue the invoice of a draft sales document.

    Returns the outbox row; its status tells the outcome. Raises
    BusinessError when nothing could be sent.
    """
    company = db.get(Company, ctx.company_id)
    cfg = get_config(db, ctx.company_id)
    if cfg is None or not cfg.is_active or not cfg.provider:
        raise BusinessError(
            "Chưa bật phát hành hóa đơn điện tử (Cài đặt → Hóa đơn điện tử)"
        )
    if not company.tax_code:
        raise BusinessError("Nhập mã số thuế của công ty trước")
    current = active_issue(db, ctx.company_id, doc.id)
    if current is not None:
        if current.status == UNKNOWN:
            return refresh(db, ctx, current)
        raise BusinessError(
            f"Chứng từ này đã có hóa đơn điện tử {current.display_number}"
        )
    entry = _check_doc(db, ctx, doc)
    if f"{today():%y}" != cfg.series[1:3]:
        raise BusinessError(
            f"Ký hiệu {cfg.series} không dành cho năm {today().year}: "
            "đăng ký ký hiệu mới với nhà cung cấp rồi sửa trong Cài đặt"
        )
    busy = db.scalar(
        select(EInvoiceIssue.id).where(
            EInvoiceIssue.company_id == ctx.company_id,
            EInvoiceIssue.series == cfg.series,
            EInvoiceIssue.status == PENDING,
            EInvoiceIssue.created_at > _now() - SERIES_GUARD,
        )
    )
    if busy:
        raise BusinessError(
            "Đang phát hành một hóa đơn khác cùng ký hiệu, thử lại sau ít phút"
        )
    key = uuid.uuid4().hex
    invoice = build_invoice(db, company, cfg, doc, key)
    original_id = None
    if invoice.is_adjustment:
        original_id = original_issue(db, doc).id
    provider = provider_for(company, cfg)
    if invoice.is_adjustment and not provider.supports_adjustment:
        raise BusinessError(
            f"Chưa hỗ trợ lập hóa đơn điều chỉnh qua {provider.name} trong "
            "phần mềm: lập trên trang của nhà cung cấp rồi nhập số hóa đơn "
            "vào chứng từ"
        )
    issue = EInvoiceIssue(
        company_id=ctx.company_id,
        sales_doc_id=doc.id,
        entry_id=entry.id,
        provider=cfg.provider,
        environment=cfg.environment,
        kind=invoice.kind,
        original_issue_id=original_id,
        idem_key=key,
        status=PENDING,
        template=invoice.template,
        series=invoice.series,
        issue_date=invoice.issue_date,
        total_amount=invoice.total,
        vat_amount=invoice.total_vat,
        request=invoice.to_dict(),
        response={},
        attempts=1,
        created_at=_now(),
        created_by=ctx.user_id,
    )
    db.add(issue)
    db.commit()  # the key is safe on disk before the provider sees it
    try:
        result = provider.issue(invoice)
    except ProviderRejectedError as e:
        issue.status = FAILED
        issue.error = e.message[:1000]
        issue.response = _trim(e.raw or {})
        issue.updated_at = _now()
        _audit(db, ctx, issue, f"Phát hành hóa đơn thất bại: {e.message}")
        db.commit()
        return issue
    except ProviderUnavailableError as e:
        issue.status = UNKNOWN
        issue.error = e.message[:1000]
        issue.updated_at = _now()
        issue.next_check_at = _now() + BACKOFF[0]
        _audit(db, ctx, issue, f"Chưa rõ kết quả phát hành: {e.message}")
        db.commit()
        return issue
    _apply_result(db, issue, result)
    _write_back(db, issue, doc)
    _audit(
        db,
        ctx,
        issue,
        f"Phát hành hóa đơn {issue.display_number} "
        f"({providers.provider_class(issue.provider).name})",
    )
    db.commit()
    _after_issue(db, ctx, cfg, issue, entry)
    return issue


def _after_issue(
    db: Session,
    ctx,
    cfg: EInvoiceConfig | None,
    issue: EInvoiceIssue,
    entry: JournalEntry,
) -> None:
    if issue.status == ISSUED:
        _store_xml(db, ctx, issue)
    if cfg is not None and cfg.auto_post and issue.number:
        db.refresh(entry)
        if entry.is_draft:
            try:
                js.post_entry(db, ctx, entry)
            except BusinessError as e:
                db.rollback()
                issue.error = (
                    "Đã phát hành hóa đơn nhưng chưa ghi sổ được: " + e.message
                )[:1000]
                db.commit()


def _audit(db: Session, ctx, issue: EInvoiceIssue, summary: str) -> None:
    write_audit(
        db,
        ctx,
        action="einvoice.issue",
        entity_type="einvoice",
        entity_id=issue.id,
        summary=summary,
        after={
            "status": issue.status,
            "number": issue.display_number,
            "key": issue.idem_key,
        },
    )


def refresh(db: Session, ctx, issue: EInvoiceIssue) -> EInvoiceIssue:
    """Look up an "unknown" invoice or fetch a pending tax code."""
    if issue.status not in (UNKNOWN, SIGNED, ISSUED):
        return issue
    company = db.get(Company, issue.company_id)
    cfg = get_config(db, issue.company_id)
    if cfg is None or cfg.provider != issue.provider:
        raise BusinessError(
            "Cấu hình nhà cung cấp đã đổi – không tra cứu được hóa đơn này"
        )
    provider = provider_for(company, cfg, environment=issue.environment)
    invoice = invoice_from_issue(issue)
    before = issue.status
    try:
        if issue.status == UNKNOWN:
            result = provider.find(invoice)
        else:
            result = provider.status(invoice, _result_of(issue))
    except ProviderRejectedError as e:
        if before != SIGNED:
            issue.error = e.message[:1000]
            _schedule_next(issue)
            db.commit()
            return issue
        # The tax authority refused to assign a code: the invoice is
        # void, the document can be corrected and issued again.
        doc = db.get(SalesDoc, issue.sales_doc_id)
        if doc is not None:
            _clear_write_back(db, doc)
        issue.status = FAILED
        issue.error = e.message[:1000]
        issue.next_check_at = None
        issue.updated_at = _now()
        _audit(db, ctx, issue, f"Hóa đơn bị từ chối: {e.message}")
        db.commit()
        return issue
    except ProviderError as e:
        issue.error = e.message[:1000]
        _schedule_next(issue)
        db.commit()
        return issue
    if result is None:
        if before == UNKNOWN and (
            _now() - _aware(issue.created_at) > timedelta(minutes=10)
        ):
            # The provider never received it: safe to issue again with
            # a new key.
            issue.status = FAILED
            issue.error = "Nhà cung cấp không nhận được hóa đơn này"
            issue.next_check_at = None
        else:
            _schedule_next(issue)
        issue.updated_at = _now()
        db.commit()
        return issue
    _apply_result(db, issue, result)
    issue.attempts = (issue.attempts or 0) + 1
    if issue.status == SIGNED:
        _schedule_next(issue)
    doc = db.get(SalesDoc, issue.sales_doc_id)
    if before == UNKNOWN and doc is not None:
        _write_back(db, issue, doc)
    if issue.status != before:
        _audit(
            db,
            ctx,
            issue,
            f"Cập nhật hóa đơn {issue.display_number}: "
            f"{STATUS_LABELS[before]} → {STATUS_LABELS[issue.status]}",
        )
    db.commit()
    if before == UNKNOWN and doc is not None and issue.number:
        entry = db.get(JournalEntry, doc.entry_id)
        _after_issue(db, ctx, cfg, issue, entry)
    elif issue.status == ISSUED and not issue.xml_path:
        _store_xml(db, ctx, issue)
    return issue


def _schedule_next(issue: EInvoiceIssue) -> None:
    n = min(max(issue.attempts or 1, 1), len(BACKOFF)) - 1
    issue.next_check_at = _now() + BACKOFF[n]
    issue.attempts = (issue.attempts or 0) + 1


def download(db: Session, ctx, issue: EInvoiceIssue, kind: str) -> bytes:
    if kind not in ("pdf", "xml"):
        raise NotFoundError()
    if issue.status not in (SIGNED, ISSUED):
        raise BusinessError("Hóa đơn chưa phát hành xong")
    if kind == "xml" and issue.xml_path:
        path = get_settings().attachments_dir / issue.xml_path
        if path.exists():
            return path.read_bytes()
    company = db.get(Company, issue.company_id)
    cfg = get_config(db, issue.company_id)
    if cfg is None or cfg.provider != issue.provider:
        raise BusinessError("Cấu hình nhà cung cấp đã đổi")
    provider = provider_for(company, cfg, environment=issue.environment)
    try:
        return provider.download(
            invoice_from_issue(issue), _result_of(issue), kind
        )
    except ProviderError as e:
        raise BusinessError(f"Không tải được hóa đơn: {e.message}") from e


def _store_xml(db: Session, ctx, issue: EInvoiceIssue) -> None:
    """Keep the issued XML (the legal original) with its SHA-256."""
    try:
        data = download(db, ctx, issue, "xml")
    except BusinessError:
        return  # retried on the next refresh
    rel = Path("einvoice") / str(issue.company_id) / f"{issue.id}.xml"
    path = get_settings().attachments_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    issue.xml_path = rel.as_posix()
    issue.xml_sha256 = hashlib.sha256(data).hexdigest()
    db.commit()


# ------------------------------------------------------------- polling


def due_issues(db: Session, now: datetime | None = None) -> list[int]:
    now = now or _now()
    return list(
        db.scalars(
            select(EInvoiceIssue.id)
            .where(
                EInvoiceIssue.status.in_((UNKNOWN, SIGNED)),
                EInvoiceIssue.next_check_at.is_not(None),
                EInvoiceIssue.next_check_at <= now,
            )
            .order_by(EInvoiceIssue.next_check_at)
            .limit(500)
            .execution_options(all_tenants=True)
        )
    )


def poll(db: Session) -> dict[str, int]:
    """Refresh every invoice whose next check is due (CLI / cron)."""
    from app.core.tenancy import Ctx, bind_tenant

    counts = {"checked": 0, "issued": 0, "failed": 0}
    for issue_id in due_issues(db):
        row = db.execute(
            select(EInvoiceIssue.company_id)
            .where(EInvoiceIssue.id == issue_id)
            .execution_options(all_tenants=True)
        ).scalar_one()
        bind_tenant(db, row)
        issue = db.get(EInvoiceIssue, issue_id)
        ctx = Ctx(company_id=row, user=None, via="cli")
        if _now() - _aware(issue.created_at) > GIVE_UP_AFTER:
            issue.next_check_at = None
            issue.error = (
                "Quá 3 ngày chưa có kết quả – kiểm tra trên trang của nhà "
                "cung cấp"
            )
            db.commit()
            continue
        try:
            refresh(db, ctx, issue)
        except BusinessError as e:
            issue.error = e.message[:1000]
            issue.next_check_at = None
            db.commit()
        counts["checked"] += 1
        if issue.status == ISSUED:
            counts["issued"] += 1
        elif issue.status == FAILED:
            counts["failed"] += 1
    return counts
