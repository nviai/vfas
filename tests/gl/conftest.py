"""One-year (2024) sample data of a service company.

Used by the general ledger and financial statement tests.
"""

from datetime import date

import pytest

from app.features.bank.service import create_bank_account
from app.features.partners import service as ps
from tests.conftest import post

M = 1_000_000


@pytest.fixture()
def year_book(db, ctx):
    """Transactions of 2024.

    The company starts bookkeeping on 12/01/2023 with no opening
    balances.
    """
    bank = create_bank_account(
        db,
        ctx,
        number="0071000123456",
        bank_name="Vietcombank",
        short_name="VCB",
    )
    kh = ps.create(
        db,
        ctx,
        ps.PartnerInput(
            name="Công ty Khách hàng A",
            is_customer=True,
            tax_code="0101243150",
        ),
    )
    ncc = ps.create(
        db,
        ctx,
        ps.PartnerInput(
            name="Công ty Thiết bị B", is_supplier=True, tax_code="0312345678"
        ),
    )
    nv = ps.create(
        db, ctx, ps.PartnerInput(name="Nguyễn Văn Nhân", is_employee=True)
    )
    b = {"debit_bank_account_id": bank.id}
    bc = {"credit_bank_account_id": bank.id}
    e = {}
    e["gop_von"] = post(
        db,
        ctx,
        date(2024, 1, 2),
        "1121",
        "4111",
        500 * M,
        "BC",
        "Nhận vốn góp",
        **b,
    )
    e["rut_tien"] = post(
        db,
        ctx,
        date(2024, 1, 3),
        "1111",
        "1121",
        50 * M,
        "CK",
        "Rút tiền gửi nhập quỹ",
        **bc,
    )
    e["mua_tscd"] = post(
        db,
        ctx,
        date(2024, 1, 10),
        "2111",
        "331",
        120 * M,
        "PK",
        "Mua máy chủ",
        credit_partner_id=ncc.id,
    )
    e["vat_tscd"] = post(
        db,
        ctx,
        date(2024, 1, 10),
        "1332",
        "331",
        12 * M,
        "PK",
        "Thuế GTGT máy chủ",
        credit_partner_id=ncc.id,
    )
    e["tra_ncc"] = post(
        db,
        ctx,
        date(2024, 1, 15),
        "331",
        "1121",
        132 * M,
        "BN",
        "Trả tiền mua máy chủ",
        debit_partner_id=ncc.id,
        cash_flow="21",
        **bc,
    )
    e["doanh_thu"] = post(
        db,
        ctx,
        date(2024, 3, 31),
        "131",
        "5113",
        200 * M,
        "PK",
        "Doanh thu dịch vụ",
        debit_partner_id=kh.id,
    )
    e["vat_ra"] = post(
        db,
        ctx,
        date(2024, 3, 31),
        "131",
        "33311",
        20 * M,
        "PK",
        "Thuế GTGT đầu ra",
        debit_partner_id=kh.id,
    )
    e["thu_kh"] = post(
        db,
        ctx,
        date(2024, 4, 15),
        "1121",
        "131",
        220 * M,
        "BC",
        "Khách hàng A thanh toán",
        credit_partner_id=kh.id,
        **b,
    )
    e["luong"] = post(
        db,
        ctx,
        date(2024, 6, 30),
        "6422",
        "334",
        60 * M,
        "PK",
        "Lương 6 tháng",
        credit_partner_id=nv.id,
    )
    e["bh_nld"] = post(
        db,
        ctx,
        date(2024, 6, 30),
        "334",
        "3383",
        6_300_000,
        "PK",
        "BHXH phần người lao động",
        debit_partner_id=nv.id,
    )
    e["tncn"] = post(
        db,
        ctx,
        date(2024, 6, 30),
        "334",
        "3335",
        1 * M,
        "PK",
        "Thuế TNCN khấu trừ",
        debit_partner_id=nv.id,
    )
    e["tra_luong"] = post(
        db,
        ctx,
        date(2024, 7, 5),
        "334",
        "1121",
        52_700_000,
        "BN",
        "Trả lương",
        debit_partner_id=nv.id,
        **bc,
    )
    e["nop_bh"] = post(
        db,
        ctx,
        date(2024, 7, 10),
        "3383",
        "1121",
        6_300_000,
        "BN",
        "Nộp BHXH",
        **bc,
    )
    e["nop_tncn"] = post(
        db,
        ctx,
        date(2024, 7, 10),
        "3335",
        "1121",
        1 * M,
        "BN",
        "Nộp thuế TNCN",
        **bc,
    )
    e["chi_vp"] = post(
        db,
        ctx,
        date(2024, 8, 20),
        "6422",
        "1111",
        5 * M,
        "PC",
        "Văn phòng phẩm",
    )
    e["vat_vp"] = post(
        db,
        ctx,
        date(2024, 8, 20),
        "1331",
        "1111",
        500_000,
        "PC",
        "Thuế GTGT văn phòng phẩm",
    )
    e["khau_hao"] = post(
        db,
        ctx,
        date(2024, 12, 31),
        "6422",
        "2141",
        24 * M,
        "PK",
        "Khấu hao năm 2024",
    )
    e["khau_tru1"] = post(
        db,
        ctx,
        date(2024, 12, 31),
        "33311",
        "1331",
        500_000,
        "PK",
        "Khấu trừ thuế GTGT",
    )
    e["khau_tru2"] = post(
        db,
        ctx,
        date(2024, 12, 31),
        "33311",
        "1332",
        12 * M,
        "PK",
        "Khấu trừ thuế GTGT TSCĐ",
    )
    e["nop_vat"] = post(
        db,
        ctx,
        date(2024, 12, 30),
        "33311",
        "1121",
        7_500_000,
        "BN",
        "Nộp thuế GTGT",
        **bc,
    )
    e["lai_tg"] = post(
        db,
        ctx,
        date(2024, 12, 31),
        "1121",
        "515",
        300_000,
        "BC",
        "Lãi tiền gửi",
        **b,
    )
    return {"entries": e, "bank": bank, "kh": kh, "ncc": ncc, "nv": nv}


# Expected results
REVENUE = 200 * M
FIN_INCOME = 300_000
EXPENSES = 60 * M + 5 * M + 24 * M
PROFIT_BEFORE_TAX = REVENUE + FIN_INCOME - EXPENSES  # 111.300.000
CIT = 22_260_000
PROFIT_AFTER_TAX = PROFIT_BEFORE_TAX - CIT  # 89.040.000
