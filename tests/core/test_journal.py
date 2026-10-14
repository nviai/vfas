"""Journal engine.

Numbering, retroactive negative balances, cancellation, period locks,
immutability, data checks, balances.
"""

from datetime import date

import pytest
from sqlalchemy import select, text

from app.core.audit import AuditLog
from app.core.errors import (
    BusinessError,
    NegativeBalanceError,
    PeriodLockedError,
)
from app.core.registry import ACCOUNTANT, CHIEF
from app.features.bank.service import create_bank_account
from app.features.journal import locks, opening, queries
from app.features.journal import service as js
from app.features.journal.models import JournalEntry
from tests.conftest import post


@pytest.fixture()
def book(db, ctx):
    """Standard data of section 8 (v1).

    PT 10M (12/01/2023), PC 3M (01/03/2023), PT 5M (05/06/2024).
    """
    pt1 = post(
        db, ctx, date(2023, 1, 12), "1111", "4111", 10_000_000, "PT", "Góp vốn"
    )
    pc1 = post(
        db, ctx, date(2023, 3, 1), "6422", "1111", 3_000_000, "PC", "Chi phí"
    )
    pt2 = post(
        db,
        ctx,
        date(2024, 6, 5),
        "1111",
        "5113",
        5_000_000,
        "PT",
        "Thu dịch vụ",
    )
    return {"PT1": pt1, "PC1": pc1, "PT2": pt2}


def test_numbering_per_type_and_year(db, ctx, book):
    assert book["PT1"].number == "PT2023-0001"
    assert book["PC1"].number == "PC2023-0001"
    assert book["PT2"].number == "PT2024-0001"
    e = post(db, ctx, date(2024, 7, 1), "1111", "711", 1_000, "PT")
    assert e.number == "PT2024-0002"
    assert [
        book["PT1"].posting_seq,
        book["PC1"].posting_seq,
        book["PT2"].posting_seq,
        e.posting_seq,
    ] == [1, 2, 3, 4]


def test_running_balance_and_sums(db, company, book):
    assert queries.balance(db, company.id, "1111") == 12_000_000
    assert (
        queries.balance(db, company.id, "111", upto=date(2023, 12, 31))
        == 7_000_000
    )
    led = queries.account_ledger(
        db, company.id, "1111", date(2024, 1, 1), date(2024, 12, 31)
    )
    assert led.opening == 7_000_000
    assert [(r.line_no, r.contra, r.balance) for r in led.rows] == [
        (3, "5113", 12_000_000)
    ]
    rows = queries.account_sums(
        db, company.id, date(2023, 1, 1), date(2024, 12, 31)
    )
    assert (
        sum(r.debit for r in rows) == sum(r.credit for r in rows) == 18_000_000
    )


def test_retroactive_negative_cash(db, ctx, book):
    with pytest.raises(NegativeBalanceError) as ei:
        post(db, ctx, date(2026, 2, 10), "6422", "1111", 15_000_000, "PC")
    assert ei.value.balance == -3_000_000
    with pytest.raises(NegativeBalanceError) as ei:
        post(db, ctx, date(2023, 4, 1), "6422", "1111", 8_000_000, "PC")
    assert ei.value.line_no == 3 and ei.value.balance == -1_000_000
    assert "01/04/2023" in ei.value.message and "dòng 3" in ei.value.message
    ok = post(db, ctx, date(2023, 4, 1), "6422", "1111", 2_000_000, "PC")
    assert ok.status == "posted"
    # The rejected voucher stays a draft without a number
    drafts = db.scalars(
        select(JournalEntry).where(JournalEntry.status == "draft")
    ).all()
    assert len(drafts) == 2 and all(d.number is None for d in drafts)


def test_cancel_rules_and_locks(db, ctx, book, ctxs):
    with pytest.raises(NegativeBalanceError):
        js.cancel_entry(db, ctx, book["PT1"], "Nhầm")
    with pytest.raises(BusinessError):
        js.cancel_entry(db, ctx, book["PC1"], "  ")
    locks.lock_period(db, ctx, 2023, 1)
    locks.lock_period(db, ctx, 2023, 2)
    locks.lock_period(db, ctx, 2023, 3)
    with pytest.raises(PeriodLockedError):
        js.cancel_entry(db, ctx, book["PC1"], "Chi nhầm")
    with pytest.raises(BusinessError):
        # March must be unlocked first
        locks.unlock_period(db, ctx, 2023, 2, "x")
    with pytest.raises(BusinessError):
        locks.unlock_period(db, ctx, 2023, 3, "")
    locks.unlock_period(db, ctx, 2023, 3, "Điều chỉnh chi phí")
    js.cancel_entry(db, ctx, book["PC1"], "Chi nhầm")
    assert book["PC1"].status == "cancelled"
    actions = [
        a.action for a in db.scalars(select(AuditLog).order_by(AuditLog.id))
    ]
    assert "period.unlock" in actions and actions[-1] == "entry.cancel"
    # periods are locked in sequence
    with pytest.raises(BusinessError):
        locks.lock_period(db, ctx, 2023, 5)


def test_posted_entries_are_immutable_in_db(db, book):
    e = book["PT1"]
    for sql in (
        "UPDATE journal_lines SET amount = 1 WHERE entry_id = :i",
        "DELETE FROM journal_entries WHERE id = :i",
        "UPDATE journal_entries SET date = '2023-02-01' WHERE id = :i",
        "UPDATE journal_entries SET status = 'draft' WHERE id = :i",
        "INSERT INTO journal_lines (company_id, entry_id, line_no,"
        " description, debit_account, credit_account, amount)"
        " SELECT company_id, id, 9, '', '1111', '711', 1"
        " FROM journal_entries WHERE id = :i",
        "DELETE FROM audit_logs",
    ):
        with pytest.raises(Exception):
            db.execute(text(sql), {"i": e.id})
            db.flush()
        db.rollback()
    # The description can still be edited (audited)
    db.execute(
        text(
            "UPDATE journal_entries SET description = 'Sửa mô tả'"
            " WHERE id = :i"
        ),
        {"i": e.id},
    )
    db.commit()


def test_validation(db, ctx, company):
    cases = [
        (js.LineIn("111", "4111", 1000), "tài khoản tổng hợp"),
        (js.LineIn("1111", "9999", 1000), "không có trong hệ thống"),
        (js.LineIn("1111", "131", 1000), "cần chọn đối tượng"),
        (js.LineIn("1121", "4111", 1000), "cần chọn tài khoản ngân hàng"),
        (js.LineIn("1111", "4111", 0), "lớn hơn 0"),
    ]
    for line, msg in cases:
        with pytest.raises(BusinessError) as ei:
            js.create_entry(
                db, ctx, js.EntryIn("PK", date(2024, 1, 1), [line]), post=True
            )
        assert msg in " ".join(
            ei.value.details.get("errors", [ei.value.message])
        ), msg
        db.rollback()
    with pytest.raises(BusinessError) as ei:
        js.create_entry(
            db,
            ctx,
            js.EntryIn(
                "PK", date(2023, 1, 11), [js.LineIn("1111", "4111", 1)]
            ),
        )
    assert "trước ngày bắt đầu" in ei.value.message


def test_bank_negative_per_account(db, ctx):
    vcb = create_bank_account(
        db,
        ctx,
        number="0071000123456",
        bank_name="Vietcombank",
        short_name="VCB",
    )
    tcb = create_bank_account(
        db,
        ctx,
        number="19033333333",
        bank_name="Techcombank",
        short_name="TCB",
    )
    post(
        db,
        ctx,
        date(2024, 1, 2),
        "1121",
        "4111",
        5_000_000,
        "BC",
        debit_bank_account_id=vcb.id,
    )
    with pytest.raises(NegativeBalanceError) as ei:
        post(
            db,
            ctx,
            date(2024, 1, 3),
            "6422",
            "1121",
            1_000,
            "BN",
            credit_bank_account_id=tcb.id,
        )
    assert "TCB" in ei.value.message
    post(
        db,
        ctx,
        date(2024, 1, 3),
        "6422",
        "1121",
        1_000_000,
        "BN",
        credit_bank_account_id=vcb.id,
    )
    # internal transfer between the two banks
    post(
        db,
        ctx,
        date(2024, 1, 4),
        "1121",
        "1121",
        2_000_000,
        "CK",
        debit_bank_account_id=tcb.id,
        credit_bank_account_id=vcb.id,
    )
    assert (
        queries.balance(
            db, ctx.company_id, "1121", exact=True, bank_account_id=vcb.id
        )
        == 2_000_000
    )
    assert (
        queries.balance(
            db, ctx.company_id, "1121", exact=True, bank_account_id=tcb.id
        )
        == 2_000_000
    )


def test_reverse_entry(db, ctx, book):
    adj = js.reverse_entry(db, ctx, book["PT2"], date(2024, 7, 1), "Thu nhầm")
    assert adj.status == "draft" and adj.reverses_id == book["PT2"].id
    ln = adj.lines[0]
    assert (ln.debit_account, ln.credit_account, ln.amount) == (
        "5113",
        "1111",
        5_000_000,
    )
    js.post_entry(db, ctx, adj)
    assert queries.balance(db, ctx.company_id, "1111") == 7_000_000


def test_opening_balances(db, ctx, company):
    rows = [
        opening.OpeningIn("1111", debit=20_000_000),
        opening.OpeningIn("4111", credit=20_000_000),
    ]
    opening.replace_opening(db, ctx, rows)
    assert opening.totals(db, company.id) == (20_000_000, 20_000_000)
    led = queries.account_ledger(
        db, company.id, "1111", date(2023, 1, 12), date(2023, 12, 31)
    )
    assert led.opening == 20_000_000 and led.rows == []
    post(db, ctx, date(2023, 2, 1), "6422", "1111", 15_000_000, "PC")
    with pytest.raises(NegativeBalanceError):
        opening.replace_opening(
            db,
            ctx,
            [
                opening.OpeningIn("1111", debit=10_000_000),
                opening.OpeningIn("4111", credit=10_000_000),
            ],
        )
    locks.lock_period(db, ctx, 2023, 1)
    with pytest.raises(BusinessError):
        opening.replace_opening(db, ctx, rows)


def test_drafts_and_delete(db, ctxs):
    ctx = ctxs[ACCOUNTANT]
    e = js.create_entry(
        db,
        ctx,
        js.EntryIn("PK", date(2024, 1, 1), [js.LineIn("6422", "331", 100)]),
        allow_incomplete=True,
    )
    js.update_draft(
        db,
        ctx,
        e,
        js.EntryIn("PK", date(2024, 1, 2), [js.LineIn("6422", "3388", 200)]),
    )
    assert e.total == 200 and e.lines[0].credit_account == "3388"
    js.delete_draft(db, ctx, e)
    assert db.get(JournalEntry, e.id) is None
    acts = [
        a.action
        for a in db.scalars(
            select(AuditLog).where(AuditLog.entity_type == "entry")
        )
    ]
    assert acts == ["entry.create", "entry.update", "entry.delete"]


def test_audit_records_actor_and_company(db, ctxs, company):
    post(db, ctxs[CHIEF], date(2024, 1, 1), "1111", "4111", 1_000, "PT")
    log = db.scalars(
        select(AuditLog).where(AuditLog.action == "entry.post")
    ).one()
    assert log.company_id == company.id and log.username and log.via == "web"
