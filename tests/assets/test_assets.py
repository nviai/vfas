"""Fixed assets and tools/supplies.

Depreciation schedule (rounding, last-month true-up), monthly charge,
rerun, disposal, allocation, checks.
"""

from datetime import date

import pytest

from app.core.errors import BusinessError, PeriodLockedError
from app.core.registry import ACCOUNTANT
from app.features.assets import service
from app.features.assets.models import (
    DISPOSED,
    IN_USE,
    RUN_ALLOCATION,
    RUN_DEPRECIATION,
)
from app.features.journal import locks, queries
from app.features.journal import service as js
from app.features.partners import service as ps
from tests.conftest import post


def test_schedule_rounding_last_month_absorbs():
    amounts = [service.scheduled_amount(10_000_000, 3, k) for k in (1, 2, 3)]
    assert (
        amounts == [3_333_333, 3_333_333, 3_333_334]
        and sum(amounts) == 10_000_000
    )
    amounts = [service.scheduled_amount(11, 3, k) for k in (1, 2, 3)]
    assert sum(amounts) == 11 and amounts == [4, 4, 3]
    assert (
        service.scheduled_amount(1000, 3, 4) == 0
        and service.scheduled_amount(1000, 3, 0) == 0
    )


@pytest.fixture()
def laptop(db, ctxs):
    ctx = ctxs[ACCOUNTANT]
    sup = ps.create(
        db, ctx, ps.PartnerInput(name="Công ty Máy tính", is_supplier=True)
    )
    buy = post(
        db,
        ctx,
        date(2024, 1, 15),
        "2111",
        "331",
        36_000_000,
        "PK",
        "Mua máy chủ",
        credit_partner_id=sup.id,
    )
    a = service.create_asset(
        db,
        ctx,
        service.AssetInput(
            name="Máy chủ Dell",
            cost=36_000_000,
            useful_life_months=36,
            acquired_date=date(2024, 1, 15),
            category="machine",
            source="purchase",
            entry_id=buy.id,
        ),
    )
    return a


def test_monthly_depreciation_and_rerun(db, ctxs, laptop, company):
    ctx = ctxs[ACCOUNTANT]
    assert laptop.code == "TS0001" and laptop.depreciation_account == "2141"
    assert laptop.depreciation_start == date(2024, 1, 1)
    run = service.execute_run(db, ctx, RUN_DEPRECIATION, 2024, 1)
    assert (
        run.entry.status == "posted"
        and run.entry.doc_type == "KH"
        and run.entry.total == 1_000_000
    )
    assert run.entry.lines[0].asset_id == laptop.id
    first_entry = run.entry.id
    # rerun: cancels the old voucher
    run = service.execute_run(db, ctx, RUN_DEPRECIATION, 2024, 1)
    assert run.entry.id != first_entry
    assert js.get_entry(db, ctx, first_entry).status == "cancelled"
    assert queries.balance(db, company.id, "2141") == -1_000_000
    for m in (2, 3):
        service.execute_run(db, ctx, RUN_DEPRECIATION, 2024, m)
    reg = service.register(db, company.id, date(2024, 3, 31))
    assert reg[0].accumulated == 3_000_000 and reg[0].remaining == 33_000_000
    # A locked period cannot be charged
    chief = ctxs["chief_accountant"]
    locks.lock_period(db, chief, 2023, 0)
    for m in range(1, 4):
        locks.lock_period(db, chief, 2024, m)
    with pytest.raises(PeriodLockedError):
        service.execute_run(db, ctx, RUN_DEPRECIATION, 2024, 3)
    missing = service.missing_months(
        db, company.id, RUN_DEPRECIATION, date(2024, 6, 30)
    )
    assert missing == [(2024, 4), (2024, 5), (2024, 6)]


def test_dispose_and_revert(db, ctxs, laptop, company):
    ctx = ctxs[ACCOUNTANT]
    for m in (1, 2, 3, 4):
        service.execute_run(db, ctx, RUN_DEPRECIATION, 2024, m)
    with pytest.raises(BusinessError):
        # March and April are already charged
        service.dispose(db, ctx, laptop, date(2024, 3, 20), "Hỏng")
    service.execute_run(db, ctx, RUN_DEPRECIATION, 2024, 4)
    run4 = service.get_run(db, company.id, RUN_DEPRECIATION, 2024, 4)
    js.cancel_entry(db, ctx, run4.entry, "Ghi giảm tài sản")
    e = service.dispose(db, ctx, laptop, date(2024, 4, 10), "Thanh lý")
    db.refresh(laptop)
    assert laptop.status == DISPOSED and e.doc_type == "TS"
    pairs = sorted(
        (ln.debit_account, ln.credit_account, ln.amount) for ln in e.lines
    )
    assert pairs == [("2141", "2111", 3_000_000), ("811", "2111", 33_000_000)]
    assert (
        queries.balance(db, company.id, "211") == 0
        and queries.balance(db, company.id, "214") == 0
    )
    # No more depreciation from the disposal month on
    assert (
        service.preview(db, company.id, RUN_DEPRECIATION, 2024, 5).lines == []
    )
    # Cancel the disposal voucher -> the asset is in use again
    js.cancel_entry(db, ctx, e, "Ghi giảm nhầm")
    db.refresh(laptop)
    assert laptop.status == IN_USE and laptop.disposal_entry_id is None


def test_start_next_month_and_opening_asset(db, ctxs, company):
    ctx = ctxs[ACCOUNTANT]
    a = service.create_asset(
        db,
        ctx,
        service.AssetInput(
            name="Xe ô tô",
            cost=600_000_000,
            useful_life_months=120,
            acquired_date=date(2023, 1, 12),
            asset_account="2111",
            category="vehicle",
            start_next_month=True,
        ),
    )
    assert a.depreciation_start == date(2023, 2, 1)
    assert (
        service.depreciation_for(a, 2023, 1) == 0
        and service.depreciation_for(a, 2023, 2) == 5_000_000
    )
    # Opening asset: 60/120 months depreciated, 300M accumulated -> 300M
    # left over 60 months
    b = service.create_asset(
        db,
        ctx,
        service.AssetInput(
            name="Nhà kho",
            cost=600_000_000,
            useful_life_months=120,
            acquired_date=date(2023, 1, 12),
            opening_accumulated=300_000_000,
            opening_months_used=60,
            category="building",
            source="opening",
        ),
    )
    assert service.monthly_rate(b) == 5_000_000
    sched = service.schedule(b)
    assert len(sched) == 60 and sched[-1][2] == 600_000_000
    with pytest.raises(BusinessError):
        service.create_asset(
            db,
            ctx,
            service.AssetInput(
                name="Sai",
                cost=0,
                useful_life_months=12,
                acquired_date=date(2023, 2, 1),
            ),
        )
    with pytest.raises(BusinessError):
        service.create_asset(
            db,
            ctx,
            service.AssetInput(
                name="Sai TK",
                cost=1000,
                useful_life_months=12,
                acquired_date=date(2023, 2, 1),
                expense_account="111",
            ),
        )


def test_cannot_change_cost_after_posted_depreciation(db, ctxs, laptop):
    ctx = ctxs[ACCOUNTANT]
    service.execute_run(db, ctx, RUN_DEPRECIATION, 2024, 1)
    inp = service.AssetInput(
        name="Máy chủ Dell R750",
        cost=40_000_000,
        useful_life_months=36,
        acquired_date=date(2024, 1, 15),
        category="machine",
        code=laptop.code,
    )
    with pytest.raises(BusinessError):
        service.update_asset(db, ctx, laptop, inp)
    inp.cost = 36_000_000
    service.update_asset(db, ctx, laptop, inp)
    assert laptop.name == "Máy chủ Dell R750"
    with pytest.raises(BusinessError):
        service.delete_asset(db, ctx, laptop)


def test_prepaid_prefill(db, ctxs, company):
    ctx = ctxs[ACCOUNTANT]
    buy = post(
        db,
        ctx,
        date(2024, 3, 5),
        "242",
        "3388",
        12_000_000,
        "PK",
        "Mua bàn ghế",
    )
    pre = service.prepaid_prefill(db, ctx, buy.id)
    assert pre[0]["amount"] == 12_000_000 and pre[0]["start_month"] == date(
        2024, 3, 1
    )
    assert pre[0]["entry_id"] == buy.id


def test_prepaid_allocation_run(db, ctxs, company):
    ctx = ctxs[ACCOUNTANT]
    post(
        db, ctx, date(2024, 3, 1), "1111", "4111", 50_000_000, "PT", "Góp vốn"
    )
    buy = post(
        db,
        ctx,
        date(2024, 3, 5),
        "242",
        "1111",
        12_000_000,
        "PC",
        "Mua bàn ghế",
    )
    p = service.create_prepaid(
        db,
        ctx,
        service.PrepaidInput(
            name="Bàn ghế văn phòng",
            amount=12_000_000,
            months=12,
            start_month=date(2024, 3, 1),
            entry_id=buy.id,
        ),
    )
    assert p.code == "CC0001"
    run = service.execute_run(db, ctx, RUN_ALLOCATION, 2024, 3)
    assert run.entry.doc_type == "PB" and run.entry.total == 1_000_000
    ln = run.entry.lines[0]
    assert (ln.debit_account, ln.credit_account) == ("6422", "242")
    assert queries.balance(db, company.id, "242") == 11_000_000
    with pytest.raises(BusinessError):
        # nothing to allocate yet
        service.execute_run(db, ctx, RUN_ALLOCATION, 2023, 6)
    from app.features.checks.router import run_checks

    res = {
        r["code"]: r
        for r in run_checks(
            db, ctx, codes={"prepaid_vs_ledger", "allocation_missing"}
        )
    }
    assert res["prepaid_vs_ledger"]["issues"] == []
    # April 2024 onwards is not allocated yet
    assert res["allocation_missing"]["issues"]


def test_assets_vs_ledger_check(db, ctxs, laptop, company):
    from app.features.checks.router import run_checks

    ctx = ctxs[ACCOUNTANT]
    res = {
        r["code"]: r for r in run_checks(db, ctx, codes={"assets_vs_ledger"})
    }
    assert res["assets_vs_ledger"]["issues"] == []
    service.create_asset(
        db,
        ctx,
        service.AssetInput(
            name="Máy in",
            cost=5_000_000,
            useful_life_months=24,
            acquired_date=date(2024, 2, 1),
        ),
    )
    res = {
        r["code"]: r for r in run_checks(db, ctx, codes={"assets_vs_ledger"})
    }
    assert (
        "khác số dư Nợ TK 211" in res["assets_vs_ledger"]["issues"][0].message
    )
