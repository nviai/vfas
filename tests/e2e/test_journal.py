"""Journal vouchers end to end.

Keyboard-only entry; cancel (reason required); adjustments; deleting
drafts.
"""

import re

from playwright.sync_api import expect

from tests.e2e.conftest import pick


def make_pk(
    s,
    description: str,
    amount: str = "3.500.000",
    *,
    debit="6422",
    credit="335",
    post: bool = True,
) -> str:
    """Enter a journal voucher using only the keyboard.

    Combobox (Down, Enter) -> Enter moves to the next cell ->
    Ctrl+Enter / Ctrl+S.
    """
    s.goto("/entries/new")
    s.page.fill("input[name=description]", description)
    pick(s, s.page.locator("main input[placeholder='TK Nợ']").first, debit)
    # Enter in the combobox picks the account and moves to the next cell
    # (credit account) without adding a row
    assert s.page.evaluate("document.activeElement.placeholder") == "TK Có"
    s.page.keyboard.type(credit)
    s.page.wait_for_timeout(400)
    s.page.wait_for_load_state("networkidle")
    s.page.keyboard.press("ArrowDown")
    s.page.keyboard.press("Enter")
    s.page.wait_for_timeout(150)
    s.page.keyboard.type(amount)
    s.page.keyboard.press("Escape")
    assert s.page.locator("main table tbody tr[data-row]").count() == 1, (
        "the grid added an extra row"
    )
    s.page.keyboard.press("Control+Enter" if post else "Control+s")
    s.page.wait_for_url(re.compile(r"/entries/\d+$"))
    s.page.wait_for_load_state("networkidle")
    return s.page.url.rsplit("/", 1)[1]


def test_journal_entry_by_keyboard(accountant):
    s = accountant
    make_pk(s, "Trích trước chi phí kiểm toán (E2E bàn phím)")
    main = s.page.locator("main")
    expect(main).to_contain_text("Đã ghi sổ")
    expect(main).to_contain_text(re.compile(r"PK\d{4}-\d{4}"))
    expect(main).to_contain_text("3.500.000")
    text = s.main_text()
    assert text.count("6422") >= 1 and "335" in text


def _menu(s, item: str):
    s.page.get_by_role("button", name="Thao tác khác").click()
    s.page.get_by_role("menuitem", name=re.compile(item)).click()


def test_cancel_requires_reason_then_reverse(accountant):
    s = accountant
    make_pk(s, "Phiếu kế toán sẽ bị hủy (E2E)", "1.200.000")
    _menu(s, "Hủy chứng từ")
    dialog = s.page.get_by_role("dialog").filter(has_text="Lý do hủy")
    expect(dialog).to_be_visible()
    # drop the browser's required attribute to check that the server
    # also requires a reason
    dialog.locator("textarea[name=reason]").evaluate(
        "el => el.removeAttribute('required')"
    )
    dialog.get_by_role("button", name="Hủy chứng từ").click()
    s.page.wait_for_load_state("networkidle")
    expect(s.page.locator("body")).to_contain_text("bắt buộc phải nhập lý do")
    expect(s.page.locator("main")).to_contain_text("Đã ghi sổ")
    _menu(s, "Hủy chứng từ")
    dialog.locator("textarea[name=reason]").fill("Nhập trùng (E2E)")
    dialog.get_by_role("button", name="Hủy chứng từ").click()
    s.page.wait_for_load_state("networkidle")
    expect(s.page.locator("main")).to_contain_text("Đã hủy")
    expect(s.page.locator("main")).to_contain_text("Nhập trùng (E2E)")

    # another voucher -> create an adjustment (reversal) -> post
    make_pk(s, "Phiếu kế toán sẽ điều chỉnh (E2E)", "800.000")
    original = re.search(r"PK\d{4}-\d{4}", s.main_text()).group(0)
    _menu(s, "Lập chứng từ điều chỉnh")
    rdialog = s.page.get_by_role("dialog").filter(
        has_text="Lập chứng từ điều chỉnh"
    )
    rdialog.locator("textarea[name=reason]").fill("Ghi nhầm kỳ (E2E)")
    rdialog.get_by_role("button", name="Tạo nháp điều chỉnh").click()
    s.page.wait_for_load_state("networkidle")
    main = s.page.locator("main")
    expect(main).to_contain_text("Nháp")
    expect(main).to_contain_text(f"Điều chỉnh {original}")
    s.page.locator("main form[action$='/post'] button").first.click()
    s.page.wait_for_load_state("networkidle")
    expect(main).to_contain_text("Đã ghi sổ")


def test_delete_draft(accountant):
    s = accountant
    entry_id = make_pk(s, "Nháp sẽ xóa (E2E)", "500.000", post=False)
    expect(s.page.locator("main")).to_contain_text("Nháp")
    _menu(s, "Xóa chứng từ nháp")
    confirm = s.page.get_by_role("alertdialog")
    expect(confirm).to_be_visible()
    confirm.get_by_role("button", name="Đồng ý").or_(
        confirm.locator("button[x-ref=ok]")
    ).first.click()
    s.page.wait_for_load_state("networkidle")
    expect(s.page.locator("body")).to_contain_text("Đã xóa chứng từ nháp")
    r = s.get(f"/entries/{entry_id}")
    assert r.status == 404
