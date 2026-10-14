"""E-invoice pages: settings, the panel on a sales document, issuing,
downloads and permissions.
"""

import re

import pytest

from app.core.dates import today
from app.core.registry import ACCOUNTANT, CHIEF, OWNER, VIEWER
from app.features.einvoice import service as svc
from app.features.einvoice.providers import simulator
from app.features.partners import service as ps
from tests.conftest import ROLE_EMAILS
from tests.sales.helpers import line, sell

SERIES = f"C{today():%y}TAB"


@pytest.fixture()
def draft(db, ctx, settings):
    settings.einvoice_simulator = True
    simulator.reset()
    customer = ps.create(
        db,
        ctx,
        ps.PartnerInput(
            name="Khách Web", is_customer=True, tax_code="0101243150"
        ),
    )
    doc = sell(
        db,
        ctx,
        today(),
        [line(2_000_000, "10", desc="Tư vấn")],
        post=False,
        partner_id=customer.id,
    )
    yield doc
    simulator.reset()


def test_settings_page_saves_and_tests_connection(web, users, draft):
    web.login(ROLE_EMAILS[OWNER])
    page = web.get("/einvoice/settings")
    assert page.status_code == 200 and "simulator" in page.text
    r = web.post(
        "/einvoice/settings",
        {
            "provider": "simulator",
            "environment": "test",
            "template": "1",
            "series": SERIES,
            "is_active": "1",
            "auto_post": "1",
            "action": "test",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert web.get("/einvoice/settings").status_code == 200
    # Accountants issue invoices but do not manage the provider account
    web.login(ROLE_EMAILS[ACCOUNTANT])
    assert web.get("/einvoice/settings").status_code == 403


def test_issue_from_sales_document(web, users, draft, db, ctx):
    svc.save_config(
        db,
        ctx,
        {
            "provider": "simulator",
            "template": "1",
            "series": SERIES,
            "is_active": "1",
            "auto_post": "1",
        },
    )
    web.login(ROLE_EMAILS[CHIEF])
    page = web.get(f"/sales/{draft.entry_id}").text
    assert f"/einvoice/entries/{draft.entry_id}/panel" in page
    panel = web.get(f"/einvoice/entries/{draft.entry_id}/panel").text
    assert f"/einvoice/entries/{draft.entry_id}/issue" in panel
    r = web.post(
        f"/einvoice/entries/{draft.entry_id}/issue", follow_redirects=False
    )
    assert r.status_code == 303
    panel = web.get(f"/einvoice/entries/{draft.entry_id}/panel").text
    assert f"{SERIES}-1" in panel and "/issue" not in panel
    issue_id = re.search(r"/einvoice/(\d+)/file\.pdf", panel).group(1)
    pdf = web.get(f"/einvoice/{issue_id}/file.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    xml = web.get(f"/einvoice/{issue_id}/file.xml")
    assert xml.status_code == 200 and b"<SHDon>1</SHDon>" in xml.content
    listing = web.get("/einvoice").text
    assert f"{SERIES}-1" in listing
    r = web.post(f"/einvoice/{issue_id}/refresh", {"next": "list"})
    assert r.status_code == 200
    # Viewers see invoices but cannot issue or refresh
    web.login(ROLE_EMAILS[VIEWER])
    assert web.get("/einvoice").status_code == 200
    assert (
        web.post(
            f"/einvoice/entries/{draft.entry_id}/issue",
            follow_redirects=False,
        ).status_code
        == 403
    )


def test_panel_without_configuration(web, users, draft):
    web.login(ROLE_EMAILS[OWNER])
    panel = web.get(f"/einvoice/entries/{draft.entry_id}/panel")
    assert panel.status_code == 200
    assert "/einvoice/settings" in panel.text and "/issue" not in panel.text
    r = web.post(
        f"/einvoice/entries/{draft.entry_id}/issue", follow_redirects=False
    )
    assert r.status_code == 303  # flash: not enabled


def test_simulator_hidden_unless_enabled(web, users, settings):
    settings.einvoice_simulator = False
    web.login(ROLE_EMAILS[OWNER])
    page = web.get("/einvoice/settings").text
    assert 'value="simulator"' not in page
