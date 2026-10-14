"""Cash receipts/payments end to end.

Quick form, missing-party error, multi-line mode + VAT invoice, PDF
printing.
"""

import io
import re

from playwright.sync_api import expect
from pypdf import PdfReader

from tests.e2e.conftest import pick


def _post(s):
    s.page.get_by_role("button", name=re.compile("Lưu và ghi sổ")).click()
    s.page.wait_for_load_state("networkidle")


def test_quick_receipt_posts_and_prints(accountant):
    s = accountant
    s.goto("/cash/new?kind=PT&mode=quick")
    s.page.select_option("#f-purpose", "711")
    amount = s.page.locator("main input[placeholder='0']").first
    amount.fill("2tr")
    amount.press("Tab")
    # the amount is spelled out in words while typing
    expect(s.page.locator("main")).to_contain_text("Hai triệu đồng")
    s.page.fill("input[name=contact_name]", "Nguyễn Văn Khách E2E")
    s.page.fill("input[name=description]", "Thu tiền thanh lý giấy vụn (E2E)")
    _post(s)
    assert re.search(r"/cash/\d+$", s.page.url), s.page.url
    main = s.page.locator("main")
    expect(main).to_contain_text("Đã ghi sổ")
    expect(main).to_contain_text("2.000.000")
    number = re.search(r"PT\d{4}-\d{4}", s.main_text()).group(0)
    entry_id = s.page.url.rsplit("/", 1)[1]
    r = s.get(f"/cash/{entry_id}/print.pdf")
    assert r.status == 200 and r.headers["content-type"].startswith(
        "application/pdf"
    )
    text = "".join(
        p.extract_text() for p in PdfReader(io.BytesIO(r.body())).pages
    )
    assert "PHIẾU THU" in text and number in text and "Hai triệu đồng" in text


def test_receipt_missing_partner_shows_error_and_keeps_data(accountant):
    s = accountant
    s.goto("/cash/new?kind=PT&mode=quick")
    s.page.select_option("#f-purpose", "131")
    amount = s.page.locator("main input[placeholder='0']").first
    amount.fill("3.250.000")
    s.page.fill("input[name=contact_name]", "Khách quên chọn đối tượng")
    s.page.fill(
        "input[name=description]", "Thu nợ khách hàng (E2E thiếu đối tượng)"
    )
    _post(s)
    main = s.page.locator("main")
    expect(main).to_contain_text("chưa ghi sổ được")
    expect(main).to_contain_text("đối tượng")
    # the entered data is still on the form
    assert (
        s.page.input_value("input[name=description]")
        == "Thu nợ khách hàng (E2E thiếu đối tượng)"
    )
    assert (
        s.page.input_value("input[name=contact_name]")
        == "Khách quên chọn đối tượng"
    )
    assert (
        "3.250.000"
        in s.page.locator("main input[placeholder='0']").first.input_value()
    )
    # pick a party and post again -> success
    pick(
        s,
        s.page.locator(
            "main input[placeholder^='Khách hàng, nhà cung cấp']"
        ).first,
        "Minh Phát",
    )
    _post(s)
    assert re.search(r"/cash/\d+$", s.page.url), s.page.url
    expect(s.page.locator("main")).to_contain_text("Đã ghi sổ")
    expect(s.page.locator("main")).to_contain_text("Minh Phát")


def test_payment_detail_mode_with_vat_invoice(accountant):
    s = accountant
    s.goto("/cash/new?kind=PC&mode=detail")
    s.page.fill("input[name=contact_name]", "Cửa hàng văn phòng phẩm E2E")
    s.page.fill(
        "input[name=description]", "Mua văn phòng phẩm có hóa đơn (E2E)"
    )
    pick(s, s.page.locator("main input[placeholder='TK Nợ']").first, "6422")
    amt = s.page.locator("main table input[placeholder='0']").first
    amt.fill("1.000.000")
    amt.press("Tab")
    s.page.keyboard.press("Escape")
    s.page.check("input[name=vat_on]")
    s.page.fill("input[name=vat_invoice_no]", "00001234")
    s.page.fill(
        "input[name=vat_partner_name]", "Công ty TNHH Văn phòng phẩm E2E"
    )
    s.page.fill("input[name=vat_partner_tax_code]", "0312345678")
    s.page.select_option("select[name=vat_rate]", "10")
    _post(s)
    assert re.search(r"/cash/\d+$", s.page.url), s.page.url
    main = s.page.locator("main")
    expect(main).to_contain_text("Đã ghi sổ")
    expect(main).to_contain_text("1331")
    expect(main).to_contain_text("1.100.000")
    expect(main).to_contain_text("00001234")
