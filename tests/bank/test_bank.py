"""Bank deposits.

BC/BN/CK vouchers, S05-DNN, statements (reading, mapping,
de-duplication, matching), suggestion rules, web.
"""

from __future__ import annotations

import io
from datetime import date

import pytest
from openpyxl import Workbook, load_workbook

from app.core.errors import NegativeBalanceError
from app.core.registry import ACCOUNTANT, CASHIER, CHIEF, VIEWER
from app.features.bank import statements as st
from app.features.bank import transfers
from app.features.bank.service import create_bank_account
from app.features.cash import books, excel
from app.features.journal import queries
from app.features.journal import service as js
from app.features.org.models import Company
from tests.cash.test_vouchers import voucher
from tests.conftest import ROLE_EMAILS


@pytest.fixture()
def vcb(db, ctx):
    return create_bank_account(
        db,
        ctx,
        number="0071000123456",
        bank_name="Vietcombank",
        short_name="VCB",
        branch="Sở giao dịch",
    )


def bank_voucher(
    db,
    ctx,
    kind,
    d,
    amount,
    account,
    bank,
    description="Giao dịch",
    post=True,
    **extra,
):
    return voucher(
        db,
        ctx,
        kind,
        d,
        amount,
        account,
        description,
        contact="",
        post=post,
        bank_account_id=bank.id,
        **extra,
    )


def transfer(db, ctx, d, amount, source, target, post=True):
    t, errors = transfers.parse_form(
        db,
        {
            "date": d.isoformat(),
            "source": source,
            "target": target,
            "amount": str(amount),
            "description": "Chuyển tiền",
        },
        db.get(Company, ctx.company_id),
    )
    assert t is not None, errors
    e = transfers.save(db, ctx, t)
    if post:
        js.post_entry(db, ctx, e)
    return e


def test_bank_vouchers_transfers_and_s05(db, ctx, vcb, company):
    bank_voucher(
        db,
        ctx,
        "BC",
        date(2024, 1, 2),
        100_000_000,
        "4111",
        vcb,
        "Nhận vốn góp",
    )
    with pytest.raises(NegativeBalanceError):
        bank_voucher(db, ctx, "BN", date(2024, 1, 3), 150_000_000, "6422", vcb)
    bank_voucher(
        db,
        ctx,
        "BN",
        date(2024, 1, 5),
        2_200_000,
        "6422",
        vcb,
        "Phí dịch vụ",
        vat_on="1",
        vat_rate="10",
        vat_invoice_no="123",
        vat_goods_amount="2000000",
        mode="quick",
    )
    # Withdraw from the bank to the cash fund, then pay in cash
    ck = transfer(
        db, ctx, date(2024, 1, 6), 30_000_000, f"bank:{vcb.id}", "cash"
    )
    assert ck.number == "CK2024-0001" and ck.lines[0].debit_account == "1111"
    with pytest.raises(NegativeBalanceError):
        transfer(
            db, ctx, date(2024, 1, 7), 31_000_000, "cash", f"bank:{vcb.id}"
        )
    assert queries.balance(db, company.id, "1111", exact=True) == 30_000_000
    bal = queries.balance(
        db, company.id, "1121", exact=True, bank_account_id=vcb.id
    )
    assert bal == 100_000_000 - 2_200_000 - 200_000 - 30_000_000
    b = books.money_book(
        db,
        company.id,
        date(2024, 1, 1),
        date(2024, 1, 31),
        form="s05",
        account="1121",
        bank_account_id=vcb.id,
    )
    assert [r.contra for r in b.rows] == ["4111", "6422", "1331", "1111"]
    assert b.closing == bal
    data = excel.book_xlsx(
        company,
        b,
        "Tháng 01/2024",
        extra_lines=["Nơi mở tài khoản giao dịch: VCB"],
    )
    ws = load_workbook(io.BytesIO(data))["S05-DNN"]
    assert ws["H12"].value == 0 and ws["H13"].value == "=H12+F13-G13"


def _csv(rows: list[list]) -> bytes:
    lines = ["Ngày giao dịch;Số tham chiếu;Diễn giải;Ghi nợ;Ghi có;Số dư"]
    for r in rows:
        lines.append(";".join(str(x) for x in r))
    return ("﻿" + "\n".join(lines) + "\nTổng cộng;;;;;\n").encode("utf-8")


def test_statement_parse_match_and_draft(db, ctx, vcb, company):
    bc = bank_voucher(
        db,
        ctx,
        "BC",
        date(2024, 3, 1),
        50_000_000,
        "4111",
        vcb,
        ref_no="FT001",
    )
    data = _csv(
        [
            [
                "01/03/2024",
                "FT001",
                "NOP VON GOP",
                "",
                "50.000.000",
                "50.000.000",
            ],
            [
                "04/03/2024",
                "FT002",
                "PHI QUAN LY TAI KHOAN T3",
                "11,000",
                "",
                "49.989.000",
            ],
            [
                "05/03/2024",
                "FT003",
                "CTY ABC THANH TOAN HD 15",
                "",
                "8.800.000",
                "58.789.000",
            ],
        ]
    )
    rows = st.read_rows(data, "saoke.csv")
    header, mapping = st.guess_mapping(rows)
    assert header == 0 and set(mapping) >= {
        "date",
        "description",
        "money_in",
        "money_out",
        "balance",
        "reference",
    }
    res = st.apply_mapping(rows, header, mapping)
    assert len(res.valid) == 3 and res.skipped == 1
    assert [(ln.money_in, ln.money_out) for ln in res.valid] == [
        (50_000_000, 0),
        (0, 11_000),
        (8_800_000, 0),
    ]
    stmt, new, dup = st.import_statement(
        db, ctx, vcb, "saoke.csv", res, header, mapping
    )
    assert (new, dup) == (3, 0)
    rec = st.reconciliation(db, company.id, vcb)
    assert (
        rec.matched_count == 1
        and rec.statement_balance == 58_789_000
        and rec.book_balance == 50_000_000
    )
    # re-importing the same file: no duplicates
    _s, new2, dup2 = st.import_statement(
        db, ctx, vcb, "saoke.csv", res, header, mapping
    )
    assert (new2, dup2) == (0, 3)
    # Suggestion rule + draft from an unmatched line -> post -> matched
    # automatically
    st.save_rule(
        db, ctx, keyword="phi quan ly", account="6422", direction="out"
    )
    fee = next(sl for sl in rec.unmatched_lines if sl.money_out == 11_000)
    assert st.suggest(db, company.id, fee).account == "6422"
    draft = st.create_draft(db, ctx, fee.id)
    assert (
        draft.doc_type == "BN"
        and draft.status == "draft"
        and draft.lines[0].debit_account == "6422"
    )
    js.post_entry(db, ctx, draft)
    db.refresh(fee)
    assert fee.status == "matched" and fee.matched_entry_id == draft.id
    # Customer receipt: no rule yet -> the draft lacks a contra account
    # and must be fixed before posting
    abc = next(
        sl for sl in st.reconciliation(db, company.id, vcb).unmatched_lines
    )
    d2 = st.create_draft(db, ctx, abc.id)
    assert d2.lines[0].credit_account == ""
    with pytest.raises(Exception):
        js.post_entry(db, ctx, d2)
    # Cancel the matched voucher -> the statement line becomes
    # unmatched again
    js.cancel_entry(db, ctx, draft, "Nhầm")
    db.refresh(fee)
    assert fee.status == "unmatched"
    _ = bc


def test_parse_amount_formats():
    assert st.parse_amount("1,000,000") == 1_000_000
    assert st.parse_amount("1.000.000") == 1_000_000
    assert st.parse_amount("1000000.00") == 1_000_000
    assert st.parse_amount("-50,000") == -50_000
    assert st.parse_amount("(2.500)") == -2_500
    assert st.parse_amount("12.5") == 12 or st.parse_amount("12.5") == 13
    assert st.parse_amount("") is None and st.parse_amount("abc") is None
    assert st.parse_day("05/03/2024 10:22:01") == date(2024, 3, 5)


def test_xlsx_statement_with_signed_amount(db, ctx, vcb):
    wb = Workbook()
    ws = wb.active
    ws.append(["SAO KÊ TÀI KHOẢN"])
    ws.append([])
    ws.append(["Transaction Date", "Description", "Amount", "Balance"])
    ws.append([date(2024, 4, 1), "Interest", 1234, 1234])
    ws.append([date(2024, 4, 2), "Transfer out", -1000, 234])
    buf = io.BytesIO()
    wb.save(buf)
    rows = st.read_rows(buf.getvalue(), "s.xlsx")
    header, mapping = st.guess_mapping(rows)
    assert header == 2 and "amount" in mapping
    res = st.apply_mapping(rows, header, mapping)
    assert [(ln.money_in, ln.money_out) for ln in res.valid] == [
        (1234, 0),
        (0, 1000),
    ]


def test_web_pages(web, users, db, ctx, vcb):
    bc = bank_voucher(db, ctx, "BC", date(2024, 1, 2), 10_000_000, "4111", vcb)
    bn = bank_voucher(
        db, ctx, "BN", date(2024, 1, 3), 1_000_000, "6422", vcb, post=False
    )
    ck = transfer(
        db,
        ctx,
        date(2024, 1, 4),
        2_000_000,
        f"bank:{vcb.id}",
        "cash",
        post=False,
    )
    web.login(ROLE_EMAILS[CHIEF])
    pages = [
        "/bank",
        "/bank/new?kind=BC",
        "/bank/new?kind=BN&mode=detail",
        "/bank/transfer/new",
        f"/bank/{bc.id}",
        f"/bank/{bn.id}/edit",
        f"/bank/{ck.id}",
        f"/bank/{ck.id}/edit",
        "/bank/book",
        f"/bank/book?bank_account_id={vcb.id}&mode=year&year=2024",
        "/bank-accounts",
        "/bank/statements",
        "/bank/statements/import",
    ]
    for url in pages:
        assert web.get(url).status_code == 200, url
    for url in (
        f"/bank/book.pdf?bank_account_id={vcb.id}&mode=year&year=2024",
        f"/bank/book.xlsx?bank_account_id={vcb.id}&mode=year&year=2024",
        f"/bank/{bn.id}/print.pdf",
    ):
        r = web.get(url)
        assert r.status_code == 200 and len(r.content) > 1000, url
    # Create a BC voucher via the web
    r = web.post(
        "/bank",
        {
            "kind": "BC",
            "mode": "quick",
            "date": "2024-01-10",
            "bank_account_id": str(vcb.id),
            "description": "Thu lãi tiền gửi",
            "account": "515",
            "amount": "125000",
            "action": "post",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303 and "/bank/" in r.headers["location"]
    # Internal transfer via the web
    r = web.post(
        "/bank/transfer",
        {
            "date": "2024-01-11",
            "source": f"bank:{vcb.id}",
            "target": "cash",
            "amount": "1tr",
            "description": "Rút tiền",
            "action": "post",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    # Import a statement via the web
    data = _csv(
        [["10/01/2024", "LAI", "TRA LAI TIEN GUI", "", "125.000", "9.125.000"]]
    )
    r = web.post(
        "/bank/statements/import",
        {"bank_account_id": str(vcb.id)},
        files={"file": ("sk.csv", data)},
        follow_redirects=False,
    )
    assert r.status_code == 303
    url = r.headers["location"]
    assert web.get(url).status_code == 200
    r = web.post(
        url,
        {
            "header_row": "1",
            "col_date": "0",
            "col_reference": "1",
            "col_description": "2",
            "col_money_out": "3",
            "col_money_in": "4",
            "col_balance": "5",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    page = web.get(
        f"/bank/statements?bank_account_id={vcb.id}&status=matched"
    ).text
    assert "TRA LAI TIEN GUI" in page
    web.get("/lang/en")
    for url in pages:
        assert web.get(url).status_code == 200, url


def test_permissions(web, users):
    web.login(ROLE_EMAILS[CASHIER])
    assert web.get("/bank").status_code == 403
    web.login(ROLE_EMAILS[VIEWER])
    assert (
        web.get("/bank").status_code == 200
        and web.get("/bank/new").status_code == 403
    )
    assert web.get("/bank/statements/import").status_code == 403
    web.login(ROLE_EMAILS[ACCOUNTANT])
    assert web.get("/bank/statements/import").status_code == 200
