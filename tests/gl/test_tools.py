"""AI tools and data checks of the gl module."""

import json
from datetime import date

import pytest

from app.core.errors import BusinessError
from app.core.registry import ACCOUNTANT, VIEWER, registry
from app.features.gl import closing
from app.features.gl.periods import make
from app.features.journal.models import JournalEntry
from tests.conftest import ROLE_EMAILS
from tests.gl.conftest import CIT, PROFIT_AFTER_TAX, REVENUE


def tool(name):
    return registry.ai_tools[name]


def test_tools_registered_with_schema():
    for name in (
        "get_trial_balance",
        "get_account_ledger",
        "get_financial_statements",
        "get_profit_and_loss",
        "create_draft_journal_entry",
    ):
        t = tool(name)
        schema = t.openai_schema()
        assert (
            schema["function"]["name"] == name
            and schema["function"]["parameters"]["type"] == "object"
        )
    assert tool("create_draft_journal_entry").write is True
    assert tool("get_trial_balance").perm == "reports.view"


def test_read_tools(db, ctx, company, year_book):
    closing.record_cit(db, ctx, date(2024, 12, 31), CIT)
    closing.run(db, ctx, make("year", 2024))
    tb = tool("get_trial_balance").func(
        db, ctx, start="2024-01-01", end="2024-12-31"
    )
    assert tb["balanced"] and any(r["account"] == "511" for r in tb["rows"])
    led = tool("get_account_ledger").func(
        db,
        ctx,
        account="131",
        start="2024-01-01",
        end="2024-12-31",
        partner_code=year_book["kh"].code,
    )
    assert led["total_debit"] == 220_000_000 and led["closing_balance"] == 0
    fs = tool("get_financial_statements").func(db, ctx, year=2024)
    assert fs["income_statement"]["60"]["current"] == PROFIT_AFTER_TAX
    assert (
        fs["balance_sheet"]["200"]["current"]
        == fs["balance_sheet"]["500"]["current"]
    )
    pl = tool("get_profit_and_loss").func(
        db, ctx, start="2024-01-01", end="2024-06-30"
    )
    assert {x["code"]: x["amount"] for x in pl["lines"]}["01"] == REVENUE
    json.dumps([tb, led, fs, pl])  # results must be serializable


def test_draft_tool_creates_draft_only(db, ctxs, year_book):
    ctx = ctxs[ACCOUNTANT]
    res = tool("create_draft_journal_entry").func(
        db,
        ctx,
        date="2024-11-30",
        description="Trích trước lãi vay",
        lines=[{"debit": "635", "credit": "335", "amount": 1_500_000}],
    )
    e = db.get(JournalEntry, res["draft_id"])
    assert (
        e.status == "draft"
        and e.source == "ai"
        and res["url"] == f"/entries/{e.id}"
    )
    with pytest.raises(BusinessError):
        tool("create_draft_journal_entry").func(
            db,
            ctx,
            date="2024-11-30",
            description="x",
            lines=[
                {
                    "debit": "131",
                    "credit": "511",
                    "amount": 1,
                    "debit_partner_code": "KHONGCO",
                }
            ],
        )
    assert not ctxs[VIEWER].can(tool("create_draft_journal_entry").perm)


def test_checks_page_shows_gl_checks(web, users, db, ctx, year_book):
    web.login(ROLE_EMAILS["chief_accountant"])
    r = web.get("/checks")
    assert r.status_code == 200
    assert "Năm đã kết thúc chưa kết chuyển lãi lỗ" in r.text
    assert "Năm 2024" in r.text  # 2024 is not closed yet
