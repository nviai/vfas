"""Login, logout, wrong password."""

from playwright.sync_api import expect

from tests.e2e.conftest import login


def test_login_logout_and_wrong_password(new_session, e2e_env):
    s = new_session()
    s.goto("/")
    assert "/login" in s.page.url
    s.page.fill("input[name=email]", e2e_env.users["owner"])
    s.page.fill("input[name=password]", "sai-mat-khau")
    s.page.click("button[type=submit]")
    expect(s.page.locator("body")).to_contain_text(
        "Email hoặc mật khẩu không đúng"
    )
    # the email is kept so only the password must be retyped
    assert s.page.input_value("input[name=email]") == e2e_env.users["owner"]

    login(s, e2e_env.users["owner"], e2e_env.password)
    expect(s.page.locator("main")).to_contain_text("Chào")
    # log out via the user menu
    s.page.locator(
        "header button[aria-haspopup], header [data-user-menu], "
        "header button:has(.rounded-full)"
    ).last.click()
    s.page.get_by_role("button", name="Đăng xuất").or_(
        s.page.get_by_role("menuitem", name="Đăng xuất")
    ).first.click()
    s.page.wait_for_url("**/login")
    s.goto("/cash")
    assert "/login" in s.page.url
