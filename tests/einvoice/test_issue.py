"""Issuing e-invoices from sales documents (simulated provider).

Covers the outbox/idempotency rules, writing the number back to the
document and its VAT lines, locking issued documents, adjustment
invoices and polling.
"""

import hashlib
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.dates import today
from app.core.errors import BusinessError
from app.features.einvoice import providers
from app.features.einvoice import service as svc
from app.features.einvoice.models import EInvoiceIssue
from app.features.einvoice.providers import simulator
from app.features.einvoice.providers.base import (
    FAILED,
    ISSUED,
    SIGNED,
    UNKNOWN,
    ProviderRejectedError,
    ProviderUnavailableError,
)
from app.features.journal import service as js
from app.features.journal.models import JournalEntry, VatRecord
from app.features.partners import service as ps
from app.features.purchases.einvoice import parse_xml
from app.features.sales import service as ss
from tests.sales.helpers import header, line, sell

SERIES = f"C{today():%y}TAA"


@pytest.fixture()
def customer(db, ctx):
    return ps.create(
        db,
        ctx,
        ps.PartnerInput(
            name="Công ty Khách Hàng A",
            is_customer=True,
            tax_code="0101243150",
            address="1 Lê Lợi, Hà Nội",
        ),
    )


@pytest.fixture()
def sim(settings, db, ctx):
    settings.einvoice_simulator = True
    simulator.reset()
    svc.save_config(
        db,
        ctx,
        {
            "provider": "simulator",
            "environment": "test",
            "template": "1",
            "series": SERIES,
            "auto_post": "1",
            "is_active": "1",
        },
    )
    yield
    simulator.reset()


def _draft(db, ctx, customer, lines=None, **kw):
    return sell(
        db,
        ctx,
        today(),
        lines
        or [
            line(10_000_000, "10", desc="Phần mềm kế toán"),
            line(5_000_000, "8", "5111", desc="Máy in"),
        ],
        post=False,
        partner_id=customer.id,
        **kw,
    )


def test_issue_writes_number_posts_and_keeps_xml(
    db, ctx, customer, sim, settings
):
    doc = _draft(db, ctx, customer)
    issue = svc.issue_for_doc(db, ctx, doc)
    assert issue.status == ISSUED and issue.number == "1"
    assert issue.tax_authority_code.startswith("MO PHONG")
    assert issue.total_amount == 15_000_000 + 1_000_000 + 400_000
    db.refresh(doc)
    assert (doc.invoice_template, doc.invoice_series, doc.invoice_no) == (
        "1",
        SERIES,
        "1",
    )
    assert doc.invoice_date == today()
    entry = db.get(JournalEntry, doc.entry_id)
    assert entry.status == "posted" and entry.ref_no == f"{SERIES}-1"
    vats = db.scalars(
        select(VatRecord).where(VatRecord.entry_id == entry.id)
    ).all()
    assert vats and all(
        (v.invoice_series, v.invoice_no) == (SERIES, "1") for v in vats
    )
    # The legal XML is stored with its hash and parses as an HDon file
    path = settings.attachments_dir / issue.xml_path
    data = path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == issue.xml_sha256
    parsed = parse_xml(data)
    assert parsed.number == "1" and parsed.total_payment == 16_400_000
    # A second invoice gets the next number
    doc2 = _draft(db, ctx, customer)
    assert svc.issue_for_doc(db, ctx, doc2).number == "2"


def test_issued_document_is_locked(db, ctx, customer, sim):
    doc = _draft(db, ctx, customer)
    svc.issue_for_doc(db, ctx, doc)
    entry = db.get(JournalEntry, doc.entry_id)
    with pytest.raises(BusinessError, match="hóa đơn điện tử"):
        js.cancel_entry(db, ctx, entry, "Nhập sai")
    db.rollback()
    with pytest.raises(BusinessError, match="đã có hóa đơn điện tử"):
        svc.issue_for_doc(db, ctx, doc)


def test_issued_draft_cannot_be_edited_or_deleted(db, ctx, customer, sim):
    cfg = svc.get_config(db, ctx.company_id)
    cfg.auto_post = False
    db.commit()
    doc = _draft(db, ctx, customer)
    svc.issue_for_doc(db, ctx, doc)
    entry = db.get(JournalEntry, doc.entry_id)
    assert entry.is_draft
    inp = ss.SalesInput(
        kind="sale",
        header=header(today(), partner_id=customer.id),
        lines=[line(1_000_000)],
    )
    with pytest.raises(BusinessError, match="không sửa"):
        ss.update(db, ctx, doc, inp)
    db.rollback()
    with pytest.raises(BusinessError, match="không xóa"):
        js.delete_draft(db, ctx, entry)
    db.rollback()
    # Posting the issued draft still works
    js.post_entry(db, ctx, db.get(JournalEntry, doc.entry_id))


def test_only_drafts_without_invoice_number(db, ctx, customer, sim):
    posted = sell(db, ctx, today(), [line(1_000_000)], partner_id=customer.id)
    with pytest.raises(BusinessError, match="nháp"):
        svc.issue_for_doc(db, ctx, posted)
    imported = _draft(db, ctx, customer, invoice_no="123")
    with pytest.raises(BusinessError, match="đã có số hóa đơn"):
        svc.issue_for_doc(db, ctx, imported)


def test_series_must_match_the_year(db, ctx, customer, sim):
    cfg = svc.get_config(db, ctx.company_id)
    cfg.series = f"C{(today().year - 1) % 100:02d}TAA"
    db.commit()
    with pytest.raises(BusinessError, match="Ký hiệu"):
        svc.issue_for_doc(db, ctx, _draft(db, ctx, customer))
    assert db.scalars(select(EInvoiceIssue)).all() == []


def test_config_validation(db, ctx, settings):
    settings.einvoice_simulator = True
    base = {"provider": "simulator", "template": "1", "series": "C26TAA"}
    with pytest.raises(BusinessError, match="6 ký tự"):
        svc.save_config(db, ctx, base | {"series": "AA"})
    with pytest.raises(BusinessError, match="nhà cung cấp"):
        svc.save_config(db, ctx, base | {"provider": "khong-co"})
    with pytest.raises(BusinessError, match="https"):
        svc.save_config(db, ctx, base | {"base_url": "http://x.vn"})
    # SaaS mode: no internal addresses (SSRF)
    with pytest.raises(BusinessError, match="nội bộ"):
        svc.save_config(db, ctx, base | {"base_url": "https://127.0.0.1"})
    settings.allow_signup = False  # self-hosted: the LAN is allowed
    svc.save_config(db, ctx, base | {"base_url": "https://10.0.0.5"})
    settings.allow_signup = True
    settings.einvoice_simulator = False
    with pytest.raises(BusinessError, match="nhà cung cấp"):
        svc.save_config(db, ctx, base)


class LostAnswer(simulator.SimulatorProvider):
    """Issues the invoice, then loses the answer (timeout)."""

    def issue(self, invoice):
        super().issue(invoice)
        raise ProviderUnavailableError("Hết thời gian chờ")


class Refuses(simulator.SimulatorProvider):
    def issue(self, invoice):
        raise ProviderRejectedError("Ký hiệu chưa đăng ký", code="E1")


def test_lost_answer_is_looked_up_not_resent(
    db, ctx, customer, sim, monkeypatch
):
    monkeypatch.setattr(providers, "provider_class", lambda code: LostAnswer)
    doc = _draft(db, ctx, customer)
    issue = svc.issue_for_doc(db, ctx, doc)
    assert issue.status == UNKNOWN and not issue.number
    assert issue.next_check_at is not None
    db.refresh(doc)
    assert doc.invoice_no == ""
    # "Issue" again = look up the same key; no second invoice
    issue = svc.issue_for_doc(db, ctx, doc)
    assert issue.status == ISSUED and issue.number == "1"
    assert db.scalars(select(EInvoiceIssue)).all() == [issue]
    db.refresh(doc)
    assert doc.invoice_no == "1"
    assert db.get(JournalEntry, doc.entry_id).status == "posted"


def test_unknown_invoice_never_received_can_be_reissued(
    db, ctx, customer, sim, monkeypatch
):
    class NeverArrives(simulator.SimulatorProvider):
        def issue(self, invoice):
            raise ProviderUnavailableError("Mất kết nối")

    monkeypatch.setattr(providers, "provider_class", lambda code: NeverArrives)
    doc = _draft(db, ctx, customer)
    issue = svc.issue_for_doc(db, ctx, doc)
    assert issue.status == UNKNOWN
    # Not found right away: keep waiting (it may still arrive)
    assert svc.refresh(db, ctx, issue).status == UNKNOWN
    issue.created_at = issue.created_at - timedelta(minutes=11)
    db.commit()
    assert svc.refresh(db, ctx, issue).status == FAILED
    monkeypatch.setattr(
        providers, "provider_class", lambda code: simulator.SimulatorProvider
    )
    assert svc.issue_for_doc(db, ctx, doc).status == ISSUED


def test_rejected_invoice_can_be_fixed_and_reissued(
    db, ctx, customer, sim, monkeypatch
):
    monkeypatch.setattr(providers, "provider_class", lambda code: Refuses)
    doc = _draft(db, ctx, customer)
    issue = svc.issue_for_doc(db, ctx, doc)
    assert issue.status == FAILED and "chưa đăng ký" in issue.error
    db.refresh(doc)
    assert doc.invoice_no == ""
    monkeypatch.setattr(
        providers, "provider_class", lambda code: simulator.SimulatorProvider
    )
    again = svc.issue_for_doc(db, ctx, doc)
    assert again.status == ISSUED and again.idem_key != issue.idem_key


def test_adjustment_invoice_for_sales_return(db, ctx, customer, sim):
    doc = _draft(db, ctx, customer)
    original = svc.issue_for_doc(db, ctx, doc)
    ret = sell(
        db,
        ctx,
        today(),
        [line(1_000_000, "10", desc="Giảm giá phần mềm")],
        post=False,
        kind="return",
        partner_id=customer.id,
        original_entry_id=doc.entry_id,
    )
    adj = svc.issue_for_doc(db, ctx, ret)
    assert adj.kind == "adjust_down" and adj.original_issue_id == original.id
    assert adj.request["original"]["number"] == original.number
    assert adj.number == "2"
    vat = db.scalars(
        select(VatRecord).where(VatRecord.entry_id == ret.entry_id)
    ).one()
    assert vat.group == "adjust_down" and vat.invoice_no == "2"


def test_return_needs_an_issued_original(db, ctx, customer, sim):
    sale = sell(
        db,
        ctx,
        today(),
        [line(2_000_000)],
        partner_id=customer.id,
        invoice_no="77",
    )
    ret = sell(
        db,
        ctx,
        today(),
        [line(1_000_000)],
        post=False,
        kind="return",
        partner_id=customer.id,
        original_entry_id=sale.entry_id,
    )
    with pytest.raises(BusinessError, match="hóa đơn gốc"):
        svc.issue_for_doc(db, ctx, ret)


def test_poll_fetches_tax_authority_code(db, ctx, customer, sim, monkeypatch):
    class Async(simulator.SimulatorProvider):
        def issue(self, invoice):
            result = super().issue(invoice)
            result.status, code = SIGNED, result.tax_authority_code
            result.tax_authority_code = ""
            Async.codes[invoice.key] = code
            return result

        def status(self, invoice, result):
            result.status = ISSUED
            result.tax_authority_code = Async.codes[invoice.key]
            return result

    Async.codes = {}
    monkeypatch.setattr(providers, "provider_class", lambda code: Async)
    issue = svc.issue_for_doc(db, ctx, _draft(db, ctx, customer))
    assert issue.status == SIGNED and issue.number == "1"
    assert not issue.tax_authority_code
    # Not due yet
    assert svc.poll(db)["checked"] == 0
    issue.next_check_at = issue.next_check_at - timedelta(minutes=1)
    db.commit()
    db.close()
    counts = svc.poll(db)
    assert counts == {"checked": 1, "issued": 1, "failed": 0}
    issue = db.get(EInvoiceIssue, issue.id)
    assert issue.status == ISSUED and issue.tax_authority_code
    assert issue.xml_sha256


def test_cli_einvoice_poll(engine, capsys):
    from app.cli import main

    assert main(["einvoice-poll"]) == 0
    assert "0 hóa đơn" in capsys.readouterr().out
