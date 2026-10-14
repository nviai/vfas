"""Data isolation between companies and permissions.

Covers role permissions and API token scopes.
"""

from datetime import date

import pytest
from sqlalchemy import select

from app.core.errors import BusinessError, NotFoundError, PermissionDeniedError
from app.core.registry import ACCOUNTANT, CASHIER, OWNER, VIEWER
from app.core.security import build_current_user
from app.core.tenancy import Ctx, bind_tenant
from app.features.journal import queries
from app.features.journal import service as js
from app.features.journal.models import JournalEntry
from app.features.partners import service as ps
from app.features.partners.models import Partner
from tests.conftest import make_company, make_user, post


@pytest.fixture()
def other(db):
    owner = make_user(db, "boss@khac.vn")
    c = make_company(db, owner, name="Công ty Khác", tax_code="0101010101")
    return c, Ctx(company_id=c.id, user=build_current_user(db, owner, c.id))


def test_data_isolated_between_companies(db, ctx, other):
    c2, ctx2 = other
    post(db, ctx, date(2024, 1, 1), "1111", "4111", 1_000_000, "PT")
    post(db, ctx2, date(2024, 1, 1), "1111", "4111", 7_000_000, "PT")
    assert queries.balance(db, ctx.company_id, "1111") == 1_000_000
    assert queries.balance(db, c2.id, "1111") == 7_000_000
    # Voucher numbers are independent per company
    nums = db.scalars(
        select(JournalEntry.number).order_by(JournalEntry.id)
    ).all()
    assert nums == ["PT2024-0001", "PT2024-0001"]
    # get_entry blocks another company's vouchers
    e2 = db.scalars(
        select(JournalEntry).where(JournalEntry.company_id == c2.id)
    ).one()
    with pytest.raises(NotFoundError):
        js.get_entry(db, ctx, e2.id)


def test_cannot_reference_other_company_partner(db, ctx, other):
    _c2, ctx2 = other
    p2 = ps.create(
        db, ctx2, ps.PartnerInput(name="KH công ty khác", is_customer=True)
    )
    with pytest.raises(BusinessError) as ei:
        js.create_entry(
            db,
            ctx,
            js.EntryIn(
                "PK",
                date(2024, 1, 1),
                [js.LineIn("131", "5113", 100, debit_partner_id=p2.id)],
            ),
        )
    assert "Đối tượng không hợp lệ" in ei.value.message


def test_bound_session_filters_and_guards(db, ctx, other):
    c2, ctx2 = other
    ps.create(db, ctx, ps.PartnerInput(name="KH A", is_customer=True))
    p2 = ps.create(db, ctx2, ps.PartnerInput(name="KH B", is_customer=True))
    db.expunge_all()
    bind_tenant(db, ctx.company_id)
    names = db.scalars(select(Partner.name)).all()
    assert names == ["KH A"]
    assert db.get(Partner, p2.id) is None
    db.add(Partner(company_id=c2.id, code="X", name="Lén"))
    with pytest.raises(PermissionDeniedError):
        db.flush()
    db.rollback()
    bind_tenant(db, None)


def test_role_permissions(users):
    assert users[OWNER].can("members.manage")
    assert users[ACCOUNTANT].can("journal.post") and not users[ACCOUNTANT].can(
        "locks.manage"
    )
    assert not users[CASHIER].can("journal.post")
    assert users[VIEWER].can("reports.view") and not users[VIEWER].can(
        "journal.create"
    )


def test_api_token_scopes(users):
    from dataclasses import replace

    owner = users[OWNER]
    ro = replace(owner, api_scopes=frozenset({"read"}))
    assert (
        ro.can("journal.view")
        and ro.can("reports.export")
        and ro.can("ai.use")
    )
    assert (
        not ro.can("journal.create")
        and not ro.can("journal.post")
        and not ro.can("settings.manage")
    )
    rw = replace(owner, api_scopes=frozenset({"read", "write_draft"}))
    assert rw.can("journal.create") and not rw.can("journal.post")
