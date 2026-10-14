"""Sales, purchases and bank end to end.

Sales (invoice grid) -> stock decreases; XML invoice import -> draft
purchase voucher; CSV statement import -> matching and drafts.
"""

import re
import time
from datetime import date
from pathlib import Path

from playwright.sync_api import expect

from tests.e2e.conftest import pick

FIXTURES = Path(__file__).resolve().parents[1] / "purchases" / "fixtures"


def _closing_qty(s, code: str) -> int:
    s.goto("/inventory/summary")
    row = s.page.locator("main table tbody tr", has_text=code).first
    cells = row.locator("td").all_inner_texts()
    return int(cells[-2].replace(".", "").strip() or 0)


def test_sale_posts_and_reduces_stock(accountant):
    s = accountant
    before = _closing_qty(s, "MH27LG")
    s.goto("/sales/new")
    pick(
        s,
        s.page.locator("main input[placeholder^='Mã, tên hoặc MST']"),
        "Minh Phát",
    )
    pick(
        s, s.page.locator("main input[placeholder='Mã hàng…']").first, "MH27LG"
    )
    row = s.page.locator("main table tbody tr").first
    inputs = row.locator("input").all()
    unit_idx = next(
        i
        for i, el in enumerate(inputs)
        if el.get_attribute("name") == "lines-0-unit"
    )
    qty = inputs[unit_idx + 1]
    qty.fill("1")
    qty.press("Tab")
    s.page.keyboard.press("Escape")
    s.page.get_by_role("button", name=re.compile("Lưu và ghi sổ")).click()
    s.page.wait_for_url(re.compile(r"/sales/\d+$"))
    main = s.page.locator("main")
    expect(main).to_contain_text("Đã ghi sổ")
    expect(main).to_contain_text(re.compile(r"BH\d{4}-\d{4}"))
    expect(main).to_contain_text("33311")
    assert _closing_qty(s, "MH27LG") == before - 1


def test_import_einvoice_xml_creates_draft_purchase(accountant, e2e_env):
    s = accountant
    number = f"{int(time.time()) % 10**8:08d}"
    xml = (FIXTURES / "hd_dau_vao_1.xml").read_text(encoding="utf-8")
    xml = xml.replace(
        "<MST>0000000001</MST>",
        f"<MST>{e2e_env.data['company_tax_code']}</MST>",
    )
    xml = xml.replace("<SHDon>00000457</SHDon>", f"<SHDon>{number}</SHDon>")
    xml = xml.replace(
        "<NLap>2026-03-10</NLap>", f"<NLap>{date.today().isoformat()}</NLap>"
    )
    s.goto("/einvoices")
    s.page.set_input_files(
        "input[name=files]",
        files=[
            {
                "name": f"hd_{number}.xml",
                "mimeType": "application/xml",
                "buffer": xml.encode("utf-8"),
            }
        ],
    )
    s.page.get_by_role("button", name="Đọc hóa đơn").click()
    s.page.wait_for_load_state("networkidle")
    main = s.page.locator("main")
    expect(main).to_contain_text("Mua vào")
    expect(main).to_contain_text("VĂN PHÒNG PHẨM SÀI GÒN")
    expect(main).to_contain_text("2.611.999")
    s.page.get_by_role(
        "button", name=re.compile(r"Tạo 1 chứng từ nháp")
    ).click()
    s.page.wait_for_load_state("networkidle")
    assert "/purchases" in s.page.url
    expect(s.page.locator("body")).to_contain_text(
        "Đã tạo 1 chứng từ mua hàng"
    )
    # Importing the same invoice again -> flagged as a duplicate,
    # nothing new is created
    s.goto("/einvoices")
    s.page.set_input_files(
        "input[name=files]",
        files=[
            {
                "name": "lai.xml",
                "mimeType": "application/xml",
                "buffer": xml.encode("utf-8"),
            }
        ],
    )
    s.page.get_by_role("button", name="Đọc hóa đơn").click()
    s.page.wait_for_load_state("networkidle")
    expect(s.page.locator("main")).to_contain_text(
        re.compile("[Tt]rùng|[Đđ]ã nhập")
    )


def _bank_receipt(s, amount: str, description: str) -> None:
    s.goto("/bank/new?kind=BC&mode=quick")
    s.page.select_option("#f-purpose", "515")
    amt = s.page.locator("main input[placeholder='0']").first
    amt.fill(amount)
    s.page.fill("input[name=description]", description)
    s.page.get_by_role("button", name=re.compile("Lưu và ghi sổ")).click()
    s.page.wait_for_url(re.compile(r"/bank/\d+$"))
    expect(s.page.locator("main")).to_contain_text("Đã ghi sổ")


def test_bank_statement_import_match_and_draft(accountant):
    s = accountant
    _bank_receipt(s, "777.123", "Lãi tiền gửi tháng (E2E sao kê)")
    today = date.today().strftime("%d/%m/%Y")
    csv = (
        "﻿Ngày giao dịch;Số tham chiếu;Diễn giải;Ghi nợ;Ghi có;Số dư\n"
        f"{today};E2E01;LAI TIEN GUI E2E;;777.123;\n"
        f"{today};E2E02;PHI QUAN LY TAI KHOAN E2E;22.000;;\n"
    )
    s.goto("/bank/statements/import")
    s.page.set_input_files(
        "input[name=file]",
        files=[
            {
                "name": "saoke_e2e.csv",
                "mimeType": "text/csv",
                "buffer": csv.encode("utf-8"),
            }
        ],
    )
    s.page.get_by_role("button", name="Đọc file").click()
    s.page.wait_for_load_state("networkidle")
    expect(s.page.locator("main")).to_contain_text("PHI QUAN LY TAI KHOAN E2E")
    s.page.get_by_role("button", name=re.compile(r"Nhập 2 giao dịch")).click()
    s.page.wait_for_load_state("networkidle")
    s.page.get_by_role("button", name="Tự khớp").click()
    s.page.wait_for_load_state("networkidle")
    # The interest line matches the BC voucher just posted; the fee line
    # is unmatched -> create a draft voucher
    base = s.page.url.split("?")[0]
    bank_id = re.search(r"bank_account_id=(\d+)", s.page.url)
    q = f"?bank_account_id={bank_id.group(1)}&" if bank_id else "?"
    s.page.goto(f"{base}{q}status=matched", wait_until="networkidle")
    expect(s.page.locator("main")).to_contain_text("LAI TIEN GUI E2E")
    s.page.goto(f"{base}{q}status=unmatched", wait_until="networkidle")
    row = (
        s.page.locator("main tr, main li")
        .filter(has_text="PHI QUAN LY TAI KHOAN E2E")
        .first
    )
    row.get_by_role("button", name="Thao tác").or_(
        row.locator("button[aria-haspopup]")
    ).first.click()
    s.page.get_by_role("menuitem", name="Lập chứng từ nháp").click()
    s.page.wait_for_load_state("networkidle")
    # Open the edit form of the draft bank payment created from the
    # statement line, with amount and description prefilled
    expect(s.page.locator("main")).to_contain_text("chi tiền gửi nháp")
    assert (
        "22.000"
        in s.page.locator("main input[placeholder='0']").first.input_value()
    )
    assert "PHI QUAN LY TAI KHOAN E2E" in s.page.input_value(
        "input[name=description]"
    )
