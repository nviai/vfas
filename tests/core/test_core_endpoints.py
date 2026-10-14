"""Shared voucher endpoints, lookups and sub-accounts.

Voucher actions: post, cancel, adjust, delete, edit description,
attachments.
"""

from datetime import date

import pytest
from sqlalchemy import select

from app.core.errors import BusinessError
from app.core.registry import ACCOUNTANT, CASHIER, CHIEF, OWNER, VIEWER
from app.features.accounts import service as accounts
from app.features.journal import service as js
from app.features.journal.models import Attachment, JournalEntry
from tests.conftest import ROLE_EMAILS, post


def _draft(
    db,
    ctx,
    doc_type="PK",
    debit="6422",
    credit="1111",
    amount=1_000_000,
    d=date(2024, 5, 2),
):
    return js.create_entry(
        db,
        ctx,
        js.EntryIn(
            doc_type, d, [js.LineIn(debit, credit, amount)], description="Nháp"
        ),
    )


def test_entry_actions_over_http(web, db, ctx, users):
    post(db, ctx, date(2024, 5, 1), "1111", "4111", 5_000_000, "PT")
    e = _draft(db, ctx)
    web.login(ROLE_EMAILS[ACCOUNTANT])
    r = web.post(f"/entries/{e.id}/post", follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    e = db.get(JournalEntry, e.id)
    assert e.status == "posted" and e.number
    # edit the description of a posted voucher
    web.post(f"/entries/{e.id}/info", {"description": "Mô tả mới"})
    db.expire_all()
    assert db.get(JournalEntry, e.id).description == "Mô tả mới"
    # cancel without a reason -> error; with a reason -> cancelled
    web.post(f"/entries/{e.id}/cancel", {"reason": ""})
    db.expire_all()
    assert db.get(JournalEntry, e.id).status == "posted"
    web.post(f"/entries/{e.id}/cancel", {"reason": "Nhập trùng"})
    db.expire_all()
    assert db.get(JournalEntry, e.id).status == "cancelled"
    # adjusting creates a reversing draft
    e2 = post(db, ctx, date(2024, 5, 3), "6422", "1111", 200_000, "PK")
    r = web.post(
        f"/entries/{e2.id}/reverse",
        {"date": "2024-05-10", "reason": "sai"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    adj = db.scalars(
        select(JournalEntry).where(JournalEntry.reverses_id == e2.id)
    ).one()
    assert adj.status == "draft" and adj.lines[0].debit_account == "1111"
    # delete the draft
    web.post(f"/entries/{adj.id}/delete")
    assert (
        db.scalar(select(JournalEntry.id).where(JournalEntry.id == adj.id))
        is None
    )


def test_attachments_upload_download_delete(web, db, ctx, users, settings):
    e = _draft(db, ctx)
    web.login(ROLE_EMAILS[CHIEF])
    r = web.post(
        f"/entries/{e.id}/attachments",
        files={"file": ("hd.pdf", b"%PDF-1.4 hello", "application/pdf")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    a = db.scalars(select(Attachment).where(Attachment.entry_id == e.id)).one()
    r = web.get(f"/attachments/{a.id}")
    assert (
        r.status_code == 200
        and r.content.startswith(b"%PDF")
        and "inline" in r.headers["content-disposition"]
    )
    # a file of the wrong type is rejected
    web.post(
        f"/entries/{e.id}/attachments",
        files={"file": ("x.exe", b"MZ\x90\x00", "application/octet-stream")},
    )
    assert (
        len(
            db.scalars(
                select(Attachment).where(Attachment.entry_id == e.id)
            ).all()
        )
        == 1
    )
    web.post(f"/attachments/{a.id}/delete")
    assert (
        db.scalar(select(Attachment.id).where(Attachment.id == a.id)) is None
    )


def test_entry_action_permissions(web, db, ctx, users):
    e = _draft(db, ctx)
    web.login(ROLE_EMAILS[VIEWER])
    assert web.post(f"/entries/{e.id}/post").status_code == 403
    assert web.post(f"/entries/{e.id}/delete").status_code == 403
    web.login(ROLE_EMAILS[CASHIER])
    # PK vouchers are outside the cashier's permissions
    assert web.post(f"/entries/{e.id}/post").status_code == 403
    db.expire_all()
    assert db.get(JournalEntry, e.id).status == "draft"


def test_lookup_endpoints(web, db, ctx, users):
    from app.features.bank.service import create_bank_account
    from app.features.items import service as items
    from app.features.partners import service as partners

    partners.create(
        db,
        ctx,
        partners.PartnerInput(
            name="Công ty Đông Á", is_customer=True, tax_code="0101243150"
        ),
    )
    partners.create(
        db,
        ctx,
        partners.PartnerInput(name="Nhà cung cấp Bắc Hà", is_supplier=True),
    )
    items.create(
        db,
        ctx,
        items.ItemInput(name="Màn hình 27 inch", kind="goods", unit="Chiếc"),
    )
    create_bank_account(
        db,
        ctx,
        number="0071000123456",
        bank_name="Vietcombank",
        short_name="VCB",
    )
    web.login(ROLE_EMAILS[OWNER])
    accs = web.get("/lookup/accounts?q=112").json()
    assert {a["value"] for a in accs} >= {"1121", "1122"} and "112" not in {
        a["value"] for a in accs
    }
    assert web.get("/lookup/accounts?postable=0&prefix=11").json()
    custs = web.get("/lookup/partners?q=đông&role=customer").json()
    assert [c["extra"]["name"] for c in custs] == ["Công ty Đông Á"]
    assert web.get("/lookup/partners?q=đông&role=supplier").json() == []
    assert web.get("/lookup/items?q=màn").json()[0]["extra"]["unit"] == "Chiếc"
    assert (
        web.get("/lookup/bank-accounts").json()[0]["extra"]["account_code"]
        == "1121"
    )
    groups = {r["group"] for r in web.get("/search?q=Đông").json()["results"]}
    assert "Đối tượng" in groups


def test_create_child_account(db, ctx):
    acc = accounts.create_child(
        db, ctx, "6422", "64221", "Chi phí nhân viên quản lý"
    )
    parent = accounts.get_account(db, ctx.company_id, "6422")
    assert (
        not parent.is_postable
        and acc.is_postable
        and acc.level == parent.level + 1
    )
    with pytest.raises(BusinessError):
        accounts.create_child(db, ctx, "6422", "6431", "Sai tiền tố")
    with pytest.raises(BusinessError):
        accounts.create_child(db, ctx, "6422", "64221", "Trùng")
    post(db, ctx, date(2024, 1, 2), "1111", "711", 1_000, "PT")
    # an account that already has postings cannot be split
    with pytest.raises(BusinessError):
        accounts.create_child(db, ctx, "711", "7111", "Thu nhập khác chi tiết")
    # the parent account no longer accepts direct postings
    with pytest.raises(BusinessError):
        js.create_entry(
            db,
            ctx,
            js.EntryIn("PK", date(2024, 1, 3), [js.LineIn("6422", "1111", 1)]),
            post=True,
        )
    accounts.update_account(db, ctx, "64221", active=False)
    with pytest.raises(BusinessError):
        js.create_entry(
            db,
            ctx,
            js.EntryIn(
                "PK", date(2024, 1, 3), [js.LineIn("64221", "1111", 1)]
            ),
            post=True,
        )
