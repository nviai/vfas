"""Platform features end to end.

Ctrl+K, keyboard shortcuts, AI assistant (fake LLM), API tokens +
OpenAI endpoint, member invitations, permissions, language/theme,
390px phone layout, company isolation.
"""

import re

import httpx
import pytest
from playwright.sync_api import expect

from tests.e2e.fake_llm_server import DRAFT_DESCRIPTION

MOBILE = {
    "viewport": {"width": 390, "height": 844},
    "is_mobile": True,
    "has_touch": True,
}


def test_command_palette_and_shortcuts(accountant):
    s = accountant
    s.goto("/")
    s.page.keyboard.press("Control+k")
    search = s.page.locator("input[x-ref=q]")
    expect(search).to_be_focused()
    s.page.keyboard.type("Minh Phát")
    option = s.page.get_by_text(
        re.compile(r"^KH\d{4} – Công ty TNHH Thương mại Minh Phát$")
    ).first
    expect(option).to_be_visible()
    option.click()
    s.page.wait_for_url(re.compile(r"/partners/\d+$"))
    expect(s.page.locator("main")).to_contain_text("Minh Phát")
    # Enter opens the first result (the feature matching the name)
    s.page.keyboard.press("Control+k")
    expect(search).to_be_focused()
    s.page.keyboard.type("Bảng cân đối tài khoản")
    s.page.wait_for_timeout(500)
    s.page.keyboard.press("Enter")
    s.page.wait_for_url(re.compile(r"/reports/f01"))
    s.goto("/")
    s.page.keyboard.press("Alt+t")
    s.page.wait_for_url(re.compile(r"/cash/new\?kind=PT"))
    expect(s.page.locator("main")).to_contain_text("Phiếu thu")


def test_ai_assistant_streams_and_drafts(accountant, e2e_env):
    s = accountant
    s.goto("/")
    s.page.keyboard.press("Control+j")
    box = s.page.locator("#ai-input")
    expect(box).to_be_visible()
    box.fill("Tồn quỹ tiền mặt hôm nay bao nhiêu?")
    box.press("Enter")
    chat = s.page.locator("[data-ai-message]").last
    expect(s.page.locator("body")).to_contain_text(
        "Xem tồn quỹ tiền mặt", timeout=15000
    )  # chip tool
    expect(s.page.locator("body")).to_contain_text(
        "Kết quả tra cứu", timeout=15000
    )
    box.fill("Hãy lập phiếu chi tạm ứng 1,5 triệu cho anh Tạm Ứng")
    box.press("Enter")
    expect(s.page.locator("body")).to_contain_text(
        "Đã lập phiếu chi nháp", timeout=15000
    )
    _ = chat
    tool_calls = [
        m
        for r in e2e_env.llm.requests
        for m in (r.get("messages") or [])
        if m.get("role") == "tool"
    ]
    assert tool_calls, "the fake LLM did not receive the tool result"
    s.goto("/cash?status=draft")
    expect(s.page.locator("main")).to_contain_text(DRAFT_DESCRIPTION)


def test_api_token_and_openai_endpoints(owner, e2e_env):
    s = owner
    s.goto("/settings/api-tokens")
    s.page.get_by_role("button", name="Tạo token").first.click()
    dialog = s.page.get_by_role("dialog").filter(has_text="Tạo API token")
    dialog.locator("input[name=name]").fill("E2E OpenAI SDK")
    dialog.get_by_role("button", name="Tạo token").click()
    s.page.wait_for_load_state("networkidle")
    token = s.page.locator("input[readonly][value^='vfas_']").input_value()
    assert token.startswith("vfas_")
    # The token is shown only once
    s.goto("/settings/api-tokens")
    assert s.page.locator(f"input[value='{token}']").count() == 0
    h = {"Authorization": f"Bearer {token}"}
    r = httpx.get(e2e_env.url("/api/v1/models"), headers=h)
    assert r.status_code == 200 and r.json()["data"][0]["id"] == "vfas-agent"
    r = httpx.post(
        e2e_env.url("/api/v1/chat/completions"),
        headers=h,
        timeout=30,
        json={
            "model": "vfas-agent",
            "messages": [{"role": "user", "content": "Tồn quỹ hôm nay?"}],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert (
        body["object"] == "chat.completion"
        and body["choices"][0]["message"]["content"]
    )
    assert (
        httpx.get(
            e2e_env.url("/api/v1/models"),
            headers={"Authorization": "Bearer vfas_sai"},
        ).status_code
        == 401
    )


def test_invite_member_and_role_permissions(new_session, e2e_env, browser):
    owner = new_session(e2e_env.users["owner"])
    owner.goto("/members")
    owner.page.get_by_role("button", name="Mời thành viên").click()
    dialog = owner.page.get_by_role("dialog").filter(has_text="Mời thành viên")
    dialog.locator("input[name=email]").fill("moi.e2e@nviai.vn")
    dialog.locator("select[name=role]").select_option("cashier")
    dialog.get_by_role("button", name="Tạo lời mời").click()
    owner.page.wait_for_load_state("networkidle")
    link = owner.page.locator("main input[readonly]").first.input_value()
    assert "/invite/" in link
    guest = new_session()
    guest.page.goto(link, wait_until="networkidle")
    expect(guest.page.locator("body")).to_contain_text("moi.e2e@nviai.vn")
    guest.page.fill("input[name=full_name]", "Thủ quỹ mới E2E")
    guest.page.fill("input[name=password]", "matkhau-moi-123")
    guest.page.locator("button[type=submit]").click()
    guest.page.wait_for_load_state("networkidle")
    assert "/invite/" not in guest.page.url and "/login" not in guest.page.url
    # Cashier: can create cash receipts, cannot see payroll / financial
    # statements
    guest.goto("/cash/new?kind=PT")
    expect(guest.page.locator("main")).to_contain_text("Phiếu thu")
    for path in ("/payroll", "/reports/b01a", "/members"):
        r = guest.get(path)
        assert r.status == 403, path


@pytest.mark.allow_console_errors
def test_viewer_cannot_post_or_edit(new_session, e2e_env):
    s = new_session(e2e_env.users["viewer"])
    s.goto("/cash?status=draft")
    first = (
        s.page.locator("main a[href^='/cash/']")
        .filter(has_text=re.compile("Nháp"))
        .first
    )
    first.click()
    s.page.wait_for_load_state("networkidle")
    assert s.page.get_by_role("button", name="Ghi sổ").count() == 0
    assert s.page.get_by_role("link", name="Sửa").count() == 0
    assert s.get("/cash/new?kind=PT").status == 403
    assert s.get("/payroll").status == 403


def test_english_and_dark_theme(new_session, e2e_env):
    s = new_session(e2e_env.users["accountant"])
    s.goto("/lang/en")
    s.goto("/")
    assert s.page.get_attribute("html", "lang") == "en"
    expect(s.page.locator("nav").first).to_contain_text("Overview")
    s.goto("/theme/dark")
    s.goto("/reports/f01")
    assert (
        "dark" in (s.page.get_attribute("html", "class") or "")
        or s.page.get_attribute("html", "data-theme") == "dark"
    )
    expect(s.page.locator("main")).to_contain_text("Trial balance")
    s.goto("/lang/vi")
    s.goto("/theme/system")


def test_mobile_layout(new_session, e2e_env):
    s = new_session(e2e_env.users["accountant"], **MOBILE)
    for path in (
        "/",
        "/cash",
        "/cash/new?kind=PC&mode=quick",
        "/sales",
        "/reports/b01a",
        "/inventory/summary",
        "/payroll",
        "/entries/new",
    ):
        s.goto(path)
        overflow = s.page.evaluate(
            "document.documentElement.scrollWidth"
            " - document.documentElement.clientWidth"
        )
        assert overflow <= 1, f"horizontal overflow of {overflow}px on {path}"
    s.goto("/")
    s.page.get_by_role(
        "button", name=re.compile("Menu|Mở menu|Điều hướng", re.I)
    ).first.click()
    expect(
        s.page.get_by_role("link", name="Phiếu thu, chi").first
    ).to_be_visible()


@pytest.mark.allow_console_errors
def test_company_isolation(new_session, e2e_env):
    owner = new_session(e2e_env.users["owner"])
    owner.goto("/cash")
    href = (
        owner.page.locator("main a[href^='/cash/']")
        .filter(has_text=re.compile(r"PT\d{4}"))
        .first.get_attribute("href")
    )
    other = new_session(e2e_env.users["other"])
    other.goto("/")
    expect(other.page.locator("body")).to_contain_text("CÔNG TY KHÁC")
    for path in (
        href,
        href + "/print.pdf",
        "/entries/" + href.rsplit("/", 1)[1],
    ):
        assert other.get(path).status == 404, path
