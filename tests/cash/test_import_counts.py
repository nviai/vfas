"""Cash book import from Excel (v1 sample) and 08a-TT cash count."""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.audit import AuditLog
from app.core.errors import BusinessError
from app.core.registry import CHIEF
from app.features.cash import books, counts, importer
from app.features.journal import queries
from app.features.journal.models import JournalEntry, OpeningBalance
from app.features.org.models import Company
from tests.cash.test_vouchers import voucher
from tests.conftest import ROLE_EMAILS
from tests.fixtures.make_fixture import (
    EXAMPLE,
    FIXTURE,
    ROWS,
    expected,
    write_book,
)


def _preview(db, company, path: Path, **opts):
    p = importer.build_preview(
        db, company, path, path.name, importer.ImportOptions(**opts)
    )
    db.rollback()
    return p


def test_fixture_import_matches_file(db, ctx, company):
    exp = expected()
    p = _preview(db, company, FIXTURE)
    assert not p.blocking and not p.error_rows, [
        r.errors for r in p.error_rows
    ]
    assert p.skipped_examples == 1
    assert (len(p.rows), p.total_in, p.total_out) == (
        exp["rows"],
        p.file_total_in,
        p.file_total_out,
    )
    assert p.closing_after == p.file_closing == exp["closing"]
    assert p.opening_mode == "set" and p.opening_file == exp["opening"]
    assert p.needs_bank and p.partner_placeholder == 4  # 131, 331, 141 (×2)

    importer.confirm_import(db, ctx, FIXTURE, FIXTURE.name, skip_errors=False)
    db.expire_all()
    entries = db.scalars(
        select(JournalEntry).order_by(
            JournalEntry.date, JournalEntry.posting_seq
        )
    ).all()
    assert len(entries) == exp["rows"] and all(
        e.status == "posted" and e.source == "import" for e in entries
    )
    assert (
        sum(e.total for e in entries if e.doc_type == "PT") == exp["total_in"]
    )
    assert (
        sum(e.total for e in entries if e.doc_type == "PC") == exp["total_out"]
    )
    ob = db.scalars(
        select(OpeningBalance).where(OpeningBalance.account == "1111")
    ).one()
    assert ob.debit == exp["opening"]
    assert (
        queries.balance(db, company.id, "1111", exact=True) == exp["closing"]
    )
    numbers = [e.number for e in entries]
    assert numbers[0] == "PT0001" and EXAMPLE[2] not in numbers
    by_number = {e.number: e for e in entries}
    # control account in the old book -> sub-account
    mapped = {
        "3338": "33382",
        "341": "3411",
    }
    for _d, kind, number, _a, _desc, contra in ROWS:
        ln = by_number[number].lines[0]
        assert (
            ln.credit_account if kind == "thu" else ln.debit_account
        ) == mapped.get(contra, contra)
    assert by_number["PC0016"].number_seq == 16
    b = books.money_book(db, company.id, date(2023, 1, 1), date(2023, 12, 31))
    assert b.opening == exp["opening"] and b.rows[-1].balance == 61_650_000
    actions = list(db.scalars(select(AuditLog.action)))
    assert (
        actions.count("entry.import") == exp["rows"]
        and actions.count("import.confirm") == 1
    )


def test_import_negative_and_missing_contra(db, ctx, company, tmp_path):
    rows = [
        (date(2023, 1, 12), "thu", "PT0001", 1_000_000, "Thu", "711"),
        (date(2023, 1, 13), "chi", "PC0001", 5_000_000, "Chi quá tồn", "6422"),
        (date(2023, 1, 14), "chi", "PC0002", 500_000, "Chi hợp lệ", "6422"),
    ]
    path = write_book(tmp_path / "x.xlsx", rows, 0, contra=False)
    p = _preview(db, company, path)
    assert p.missing_contra == {"PT": 1, "PC": 2}
    p = _preview(
        db,
        company,
        path,
        default_receipt_contra="711",
        default_payment_contra="6422",
    )
    bad = [r for r in p.rows if not r.ok]
    assert [r.number for r in bad] == ["PC0001"] and "âm quỹ" in bad[0].errors[
        0
    ].text
    with pytest.raises(BusinessError):
        importer.confirm_import(
            db, ctx, path, "x.xlsx", options=p.options, skip_errors=False
        )
    importer.confirm_import(
        db, ctx, path, "x.xlsx", options=p.options, skip_errors=True
    )
    assert queries.balance(db, company.id, "1111", exact=True) == 500_000


def test_import_is_atomic(db, ctx, company, monkeypatch):
    calls = {"n": 0}
    real = importer.write_audit

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 5:
            raise RuntimeError("failure midway")
        return real(*args, **kwargs)

    monkeypatch.setattr(importer, "write_audit", flaky)
    with pytest.raises(RuntimeError):
        importer.confirm_import(
            db, ctx, FIXTURE, FIXTURE.name, skip_errors=False
        )
    db.rollback()
    assert db.scalar(select(JournalEntry.id).limit(1)) is None


def test_import_web(web, users, db):
    web.login(ROLE_EMAILS[CHIEF])
    r = web.post(
        "/cash/import",
        files={"file": ("so_quy.xlsx", FIXTURE.read_bytes())},
        follow_redirects=False,
    )
    assert r.status_code == 303
    url = r.headers["location"]
    page = web.get(url)
    assert page.status_code == 200 and "PT0001" in page.text
    token = url.rsplit("/", 1)[1]
    r = web.post(f"/cash/import/{token}/confirm", {}, follow_redirects=False)
    assert r.status_code == 303
    assert web.get(url).status_code == 404  # session already processed
    r = web.post(
        "/cash/import",
        files={"file": ("a.txt", b"hello")},
        follow_redirects=False,
    )
    assert r.status_code == 303 and "vfas_flash" in r.headers.get(
        "set-cookie", ""
    )


def test_cash_count_suggestion_flow(db, ctx, company, web, users):
    voucher(db, ctx, "PT", date(2024, 5, 2), 1_234_000, "4111")
    inp, errors = counts.parse_form(
        {
            "count_date": "2024-05-02",
            "count_time": "17:30",
            "qty_500000": "2",
            "qty_200000": "1",
            "qty_10000": "3",
            "qty_1000": "4",
        },
        company,
    )
    assert not errors and inp.actual_total == 1_234_000
    cc = counts.create_cash_count(db, ctx, inp)
    assert cc.number == "KK2024-001" and cc.difference == 0
    inp2, _ = counts.parse_form(
        {"count_date": "2024-05-03", "qty_500000": "2", "qty_200000": "1"},
        company,
    )
    cc2 = counts.create_cash_count(db, ctx, inp2)
    remaining = counts.remaining_difference(db, cc2)
    assert remaining == -34_000
    s = counts.suggestion(cc2, remaining)
    assert (s.kind, s.debit, s.credit, s.amount) == (
        "PC",
        "1381",
        "1111",
        34_000,
    )
    # Create the suggested cash payment via the web -> the difference is
    # settled
    web.login(ROLE_EMAILS[CHIEF])
    assert web.get(s.url).status_code == 200
    r = web.post(
        "/cash",
        {
            "kind": "PC",
            "mode": "quick",
            "date": "2024-05-03",
            "contact_name": "Thủ quỹ",
            "description": s.reason,
            "account": "1381",
            "amount": "34000",
            "action": "post",
            "cash_count_id": str(cc2.id),
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.expire_all()
    assert counts.remaining_difference(db, cc2) == 0
    from pypdf import PdfReader

    pdf = web.get(f"/cash/counts/{cc2.id}/print.pdf").content
    assert (
        "BẢNG KIỂM KÊ QUỸ"
        in PdfReader(io.BytesIO(pdf)).pages[0].extract_text()
    )
    assert web.get(f"/cash/counts/{cc2.id}").status_code == 200
    _ = Company
