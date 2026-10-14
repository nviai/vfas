"""E-invoice issuing end to end (simulated provider).

Settings page -> draft sale -> the HTMX e-invoice card -> issue with the
confirmation dialog -> number, tax code and PDF download.
"""

import re
from datetime import date

from playwright.sync_api import expect


def test_issue_einvoice_with_simulator(owner, e2e_env):
    s = owner
    series = f"C{date.today():%y}TAA"
    s.goto("/einvoice/settings")
    page = s.page
    page.select_option("select[name=provider]", "simulator")
    page.fill("input[name=series]", series)
    page.locator("#f-is_active").click()  # switch on
    page.get_by_role("button", name="Lưu cấu hình").click()
    page.wait_for_load_state("networkidle")
    expect(page.locator("#f-is_active")).to_have_attribute(
        "aria-checked", "true"
    )

    s.goto(f"/sales/{e2e_env.data['einvoice_entry_id']}")
    card = page.locator("main").get_by_text("Hóa đơn điện tử").first
    expect(card).to_be_visible()
    issue = page.get_by_role("button", name="Phát hành HĐĐT")
    expect(issue).to_be_visible()
    issue.click()
    dialog = page.get_by_role("alertdialog")
    expect(dialog).to_be_visible()
    dialog.locator("button[x-ref=ok]").click()
    page.wait_for_load_state("networkidle")
    main = page.locator("main")
    expect(main).to_contain_text(re.compile(rf"1{series}-1"))
    expect(main).to_contain_text("Đã cấp mã")
    expect(main).to_contain_text("Đã ghi sổ")  # posted after issuing
    href = main.locator("a[href*='/file.pdf']").first.get_attribute("href")
    r = s.get(href)
    assert r.status == 200 and r.body().startswith(b"%PDF")

    s.goto("/einvoice")
    expect(page.locator("main")).to_contain_text(f"1{series}-1")
