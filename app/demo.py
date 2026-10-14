"""Sample data: a small trading and services company.

The company runs from 1 January of last year up to today. Every
transaction goes through the real service of its module (no raw SQL
inserts), so the sample data doubles as an integration test: opening
balances, purchases and sales with VAT (10%, 8%), inventory receipts
and issues with costing, fixed assets with depreciation, tools with
allocation, payroll with insurance and personal income tax, cash and
bank receipts/payments, internal transfers, quarterly 01/GTGT VAT
returns, corporate income tax, closing entries and locking of last
year, a few draft documents and one cash count report.

    build_demo(db, ctx)     # ctx: Ctx of the owner/chief accountant
    python -m app.cli demo  # create the demo user and company
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.dates import month_bounds, today
from app.core.errors import BusinessError
from app.features.journal import queries
from app.features.journal import service as js
from app.features.journal.models import JournalEntry
from app.features.org.models import Company

OPENING_CASH = 50_000_000
OPENING_VCB = 600_000_000


@dataclass
class _Ctx:
    """Catalog records created up front and reused by transactions."""

    vcb: object = None
    tcb: object = None
    customers: list = field(default_factory=list)
    suppliers: list = field(default_factory=list)
    items: dict = field(default_factory=dict)
    sales_for_return: object = None
    invoice_seq: dict = field(default_factory=dict)
    runs: dict = field(default_factory=dict)


def _day(y: int, m: int, d: int) -> date:
    return date(y, m, min(d, month_bounds(y, m)[1].day))


def _round_k(v: int) -> int:
    return int(v // 1000 * 1000)


def _invoice(state: _Ctx, series: str) -> str:
    state.invoice_seq[series] = state.invoice_seq.get(series, 0) + 1
    return f"{state.invoice_seq[series]:07d}"


# ---------------------------------------------------- catalog


def _catalog(db: Session, ctx, state: _Ctx, start: date) -> None:
    from app.features.bank.service import create_bank_account
    from app.features.items import service as items
    from app.features.partners import service as partners
    from app.features.payroll import service as payroll

    state.vcb = create_bank_account(
        db,
        ctx,
        number="0071000856321",
        bank_name="Ngân hàng TMCP Ngoại thương Việt Nam",
        short_name="VCB",
        branch="Chi nhánh Sài Gòn",
    )
    state.tcb = create_bank_account(
        db,
        ctx,
        number="19036587412019",
        bank_name="Ngân hàng TMCP Kỹ thương Việt Nam",
        short_name="TCB",
        branch="Chi nhánh Quận 7",
    )
    partner_input = partners.PartnerInput
    state.customers = [
        partners.create(
            db,
            ctx,
            partner_input(
                name="Công ty CP Giải pháp Số Sài Gòn",
                is_customer=True,
                tax_code="0312345678",
                address="45 Lê Lợi, phường Bến Nghé, Quận 1, TP. Hồ Chí Minh",
                phone="028 3822 1234",
                payment_terms_days=30,
            ),
        ),
        partners.create(
            db,
            ctx,
            partner_input(
                name="Công ty TNHH Thương mại Minh Phát",
                is_customer=True,
                tax_code="0316789012",
                address="12 Nguyễn Thị Thập, Quận 7, TP. Hồ Chí Minh",
                payment_terms_days=15,
            ),
        ),
        partners.create(
            db,
            ctx,
            partner_input(
                name="Trường Cao đẳng Công nghệ Thủ Đức",
                is_customer=True,
                tax_code="0301234567",
                address="53 Võ Văn Ngân, TP. Thủ Đức, TP. Hồ Chí Minh",
                payment_terms_days=45,
            ),
        ),
    ]
    state.suppliers = [
        partners.create(
            db,
            ctx,
            partner_input(
                name="Công ty TNHH Phân phối Thiết bị Tin học Phong Vũ",
                is_supplier=True,
                tax_code="0304998358",
                address="264 Nguyễn Thị Minh Khai, Quận 3, TP. Hồ Chí Minh",
                payment_terms_days=30,
            ),
        ),
        partners.create(
            db,
            ctx,
            partner_input(
                name="Công ty CP Nội thất Hòa Phát",
                is_supplier=True,
                tax_code="0100101308",
                address="Khu công nghiệp Phố Nối A, Hưng Yên",
            ),
        ),
        partners.create(
            db,
            ctx,
            partner_input(
                name="Công ty TNHH Bất động sản Sài Gòn Riverside",
                is_supplier=True,
                tax_code="0315552468",
                address="1 Nguyễn Văn Linh, Quận 7, TP. Hồ Chí Minh",
            ),
        ),
    ]
    item_input = items.ItemInput
    state.items = {
        "laptop": items.create(
            db,
            ctx,
            item_input(
                name="Máy tính xách tay Dell Latitude 5440",
                code="LT5440",
                unit="Chiếc",
                kind="goods",
                vat_rate="10",
                sale_price=Decimal(22_000_000),
                purchase_price=Decimal(18_000_000),
            ),
        ),
        "monitor": items.create(
            db,
            ctx,
            item_input(
                name="Màn hình LG 27 inch 27UP850",
                code="MH27LG",
                unit="Chiếc",
                kind="goods",
                vat_rate="10",
                sale_price=Decimal(6_000_000),
                purchase_price=Decimal(4_500_000),
            ),
        ),
        "keyboard": items.create(
            db,
            ctx,
            item_input(
                name="Bàn phím cơ Logitech MX Mechanical",
                code="BPLOGI",
                unit="Chiếc",
                kind="goods",
                vat_rate="8",
                sale_price=Decimal(1_200_000),
                purchase_price=Decimal(900_000),
            ),
        ),
        "deploy": items.create(
            db,
            ctx,
            item_input(
                name="Dịch vụ triển khai phần mềm quản trị",
                code="DVTK",
                unit="Gói",
                kind="service",
                vat_rate="10",
                sale_price=Decimal(25_000_000),
            ),
        ),
        "training": items.create(
            db,
            ctx,
            item_input(
                name="Dịch vụ đào tạo chuyển đổi số",
                code="DVDT",
                unit="Khóa",
                kind="service",
                vat_rate="8",
                sale_price=Decimal(18_000_000),
            ),
        ),
    }
    employee_input = payroll.EmployeeInput
    for name, dep, pos, salary, deps, acc in (
        ("Nguyễn Văn Bình", "Ban giám đốc", "Giám đốc", 30_000_000, 2, "6422"),
        (
            "Trần Thị Hồng Nhung",
            "Kế toán",
            "Kế toán trưởng",
            18_000_000,
            1,
            "6422",
        ),
        (
            "Lê Minh Quân",
            "Kinh doanh",
            "Trưởng nhóm bán hàng",
            15_000_000,
            0,
            "6421",
        ),
        (
            "Phạm Thu Trang",
            "Kỹ thuật",
            "Kỹ sư triển khai",
            16_000_000,
            1,
            "6422",
        ),
    ):
        payroll.create_employee(
            db,
            ctx,
            employee_input(
                full_name=name,
                department=dep,
                position=pos,
                base_salary=salary,
                insurance_salary=salary,
                dependents=deps,
                expense_account=acc,
                taxable_allowance=1_000_000,
                nontaxable_allowance=730_000,
                start_date=start,
            ),
        )


def _opening(db: Session, ctx, state: _Ctx) -> None:
    from app.features.journal.opening import OpeningIn, replace_opening

    replace_opening(
        db,
        ctx,
        [
            OpeningIn("1111", debit=OPENING_CASH),
            OpeningIn("1121", debit=OPENING_VCB, bank_account_id=state.vcb.id),
            OpeningIn("4111", credit=OPENING_CASH + OPENING_VCB),
        ],
    )


# ---------------------------------------------------- purchases, sales


def _grid(lines: list[dict]) -> dict:
    form = {"lines-count": str(len(lines))}
    for i, ln in enumerate(lines):
        for k, v in ln.items():
            form[f"lines-{i}-{k}"] = "" if v is None else str(v)
    return form


def _purchase(
    db: Session,
    ctx,
    state: _Ctx,
    d: date,
    supplier,
    lines: list[dict],
    *,
    method: str = "credit",
    series: str = "",
    description: str = "",
) -> JournalEntry:
    from app.features.purchases import service as purchases
    from app.features.purchases.models import PURCHASE

    series = series or f"C{d.year % 100}TAA"
    form = {
        "date": d,
        "invoice_date": d,
        "partner_id": supplier.id,
        "payment_method": method,
        "bank_account_id": state.vcb.id if method == "bank" else "",
        "invoice_template": "1",
        "invoice_series": series,
        "invoice_no": _invoice(state, f"{supplier.id}-{series}"),
        "description": description,
        **_grid(lines),
    }
    inp, errors, _ = purchases.parse_form(db, ctx, form, PURCHASE)
    if inp is None:
        raise BusinessError(f"Dữ liệu mẫu mua hàng lỗi: {errors}")
    doc = purchases.create(db, ctx, inp, post=True)
    return db.get(JournalEntry, doc.entry_id)


def _sale(
    db: Session,
    ctx,
    state: _Ctx,
    d: date,
    customer,
    lines: list[dict],
    *,
    method: str = "credit",
    description: str = "",
    post: bool = True,
    kind: str | None = None,
    original_entry_id: int | None = None,
):
    from app.features.sales import service as sales
    from app.features.sales.models import SALE

    series = f"C{d.year % 100}TNV"
    form = {
        "date": d,
        "invoice_date": d,
        "payment_method": method,
        "bank_account_id": state.vcb.id if method == "bank" else "",
        "invoice_template": "1",
        "invoice_series": series,
        "invoice_no": _invoice(state, series),
        "description": description,
        "original_entry_id": original_entry_id or "",
        **_grid(lines),
    }
    if customer is not None:
        form["partner_id"] = customer.id
    else:
        form["partner_name"] = "Khách lẻ"
    inp, errors, _ = sales.parse_form(db, ctx, form, kind or SALE)
    if inp is None:
        raise BusinessError(f"Dữ liệu mẫu bán hàng lỗi: {errors}")
    return sales.create(db, ctx, inp, post=post)


def _item_line(
    state: _Ctx, key: str, qty, price: int | None = None, **extra
) -> dict:
    it = state.items[key]
    return {
        "item_id": it.id,
        "quantity": qty,
        "unit_price": price if price is not None else int(it.sale_price),
        "vat_rate": it.vat_rate,
        **extra,
    }


# ---------------------------------------------------- cash and bank


def _money(
    db: Session,
    ctx,
    kind: str,
    d: date,
    lines: list[tuple],
    description: str,
    *,
    bank=None,
    partner=None,
    contact: str = "",
    vat=None,
    post: bool = True,
) -> JournalEntry:
    from app.features.cash import vouchers as v

    voucher = v.MoneyVoucher(
        kind=kind,
        date=d,
        doc_date=d,
        description=description,
        lines=[
            v.ContraLine(
                account=a, amount=amt, description=desc, partner_id=pid
            )
            for a, amt, desc, pid in lines
        ],
        contact_name=contact or (partner.name if partner else ""),
        contact_address=partner.address if partner else "",
        partner_id=partner.id if partner else None,
        bank_account_id=bank.id if bank else None,
        vat=vat,
    )
    return v.save(db, ctx, voucher, post=post)


def _transfer(
    db: Session,
    ctx,
    d: date,
    source: str,
    target: str,
    amount: int,
    description: str,
) -> None:
    from app.features.bank import transfers

    e = transfers.save(
        db,
        ctx,
        transfers.TransferIn(
            date=d,
            doc_date=d,
            source=source,
            target=target,
            amount=amount,
            description=description,
        ),
    )
    js.post_entry(db, ctx, e)


def _credit_balance(db: Session, cid: int, account: str, d: date, **kw) -> int:
    return -queries.balance(db, cid, account, exact=True, upto=d, **kw)


# ---------------------------------------------------- period runs


def _month_end_runs(db: Session, ctx, state: _Ctx, y: int, m: int) -> None:
    from app.features.assets.models import RUN_ALLOCATION, RUN_DEPRECIATION
    from app.features.assets.service import execute_run
    from app.features.inventory.costing import run_costing
    from app.features.payroll import service as payroll

    run = payroll.create_run(db, ctx, y, m)
    payroll.post_run(db, ctx, run)
    state.runs[(y, m)] = run
    for kind in (RUN_DEPRECIATION, RUN_ALLOCATION):
        try:
            execute_run(db, ctx, kind, y, m)
        except BusinessError:
            db.rollback()
    run_costing(db, ctx, y, m)


def _quarter_vat(db: Session, ctx, company: Company, end: date) -> None:
    from app.features.vat import service as vat

    period = vat.period_of(company, end)
    try:
        e = vat.create_offset_entry(db, ctx, period)
        js.post_entry(db, ctx, e)
    except BusinessError:
        db.rollback()
    vat.save(db, ctx, period, manual={}, carried_override=None, finalize=True)


def _pay_obligations(
    db: Session, ctx, state: _Ctx, d: date, prev_end: date, *, vat_due: bool
) -> None:
    """Days 10-20: pay last month's salaries, insurance and PIT.

    The quarterly VAT is paid on day 20 when it falls due.
    """
    from app.features.payroll import service as payroll

    cid = ctx.company_id
    run = state.runs.get((prev_end.year, prev_end.month))
    if run is not None:
        pay = payroll.create_payment(
            db, ctx, run, "bank", d, bank_account_id=state.vcb.id
        )
        js.post_entry(db, ctx, pay)
    lines = []
    for acc, label in (
        ("3383", "BHXH"),
        ("3384", "BHYT"),
        ("3385", "BHTN"),
        ("3382", "KPCĐ"),
    ):
        amt = _credit_balance(db, cid, acc, prev_end)
        if amt > 0:
            lines.append(
                (
                    acc,
                    amt,
                    f"Nộp {label} tháng {prev_end.month:02d}/{prev_end.year}",
                    None,
                )
            )
    if lines:
        _money(
            db,
            ctx,
            "BN",
            d,
            lines,
            (
                "Nộp bảo hiểm, kinh phí công đoàn tháng "
                f"{prev_end.month:02d}/{prev_end.year}"
            ),
            bank=state.vcb,
            contact="Bảo hiểm xã hội Quận 7",
        )
    pit = _credit_balance(db, cid, "3335", prev_end)
    if pit > 0:
        _money(
            db,
            ctx,
            "BN",
            d,
            [("3335", pit, "Nộp thuế TNCN", None)],
            f"Nộp thuế TNCN tháng {prev_end.month:02d}/{prev_end.year}",
            bank=state.vcb,
            contact="Kho bạc Nhà nước TP. Hồ Chí Minh",
        )
    if vat_due:
        vat_amt = _credit_balance(db, cid, "33311", prev_end)
        if vat_amt > 0:
            _money(
                db,
                ctx,
                "BN",
                _day(d.year, d.month, 20),
                [("33311", vat_amt, "Nộp thuế GTGT", None)],
                (
                    "Nộp thuế GTGT quý "
                    f"{(prev_end.month - 1) // 3 + 1}/{prev_end.year}"
                ),
                bank=state.vcb,
                contact="Kho bạc Nhà nước TP. Hồ Chí Minh",
            )


def _month(
    db: Session,
    ctx,
    state: _Ctx,
    company: Company,
    rnd: random.Random,
    y: int,
    m: int,
    now: date,
) -> None:
    from app.features.cash.vouchers import VatInfo

    cid = ctx.company_id
    ncc_tb, ncc_nt, ncc_bds = state.suppliers
    kh1, kh2, kh3 = state.customers
    first = date(y, m, 1)
    prev_end = first - timedelta(days=1)

    def ok(d: date) -> bool:
        return d <= now

    # Day 2: goods receipt (purchase on credit)
    d = _day(y, m, 2)
    if ok(d):
        _purchase(
            db,
            ctx,
            state,
            d,
            ncc_tb,
            [
                _item_line(state, "laptop", rnd.randint(4, 5), 18_000_000),
                _item_line(state, "monitor", rnd.randint(8, 10), 4_500_000),
                _item_line(state, "keyboard", 12, 900_000, vat_rate="10"),
            ],
            description=f"Nhập hàng tháng {m:02d}/{y}",
        )
    # Day 4: internal transfers
    d = _day(y, m, 4)
    if ok(d):
        _transfer(
            db,
            ctx,
            d,
            f"bank:{state.vcb.id}",
            "cash",
            10_000_000,
            "Rút tiền gửi VCB về nhập quỹ",
        )
        _transfer(
            db,
            ctx,
            d,
            f"bank:{state.vcb.id}",
            f"bank:{state.tcb.id}",
            15_000_000,
            "Chuyển tiền từ VCB sang TCB",
        )
    # Day 6: credit sale to a major customer
    d = _day(y, m, 6)
    if ok(d):
        doc = _sale(
            db,
            ctx,
            state,
            d,
            kh1 if m % 2 else kh2,
            [
                _item_line(state, "laptop", rnd.randint(3, 4)),
                _item_line(state, "monitor", rnd.randint(5, 7)),
                _item_line(state, "keyboard", rnd.randint(8, 10)),
                _item_line(
                    state,
                    "deploy",
                    1,
                    rnd.choice(
                        [80_000_000, 95_000_000, 110_000_000, 120_000_000]
                    ),
                ),
            ],
        )
        if (y, m) == (now.year - 1, 5):
            state.sales_for_return = doc
    # Day 8: monthly operations support for the other customer
    d = _day(y, m, 8)
    if ok(d):
        _sale(
            db,
            ctx,
            state,
            d,
            kh2 if m % 2 else kh1,
            [
                _item_line(
                    state,
                    "deploy",
                    1,
                    rnd.choice([60_000_000, 70_000_000, 80_000_000]),
                    description=(
                        "Dịch vụ hỗ trợ vận hành, bảo trì phần mềm hằng tháng"
                    ),
                )
            ],
        )
    # Day 10: pay salaries, insurance and PIT (and last quarter's VAT
    # on day 20)
    d = _day(y, m, 10)
    if ok(d) and prev_end >= company.start_date:
        vat_due = prev_end.month in (3, 6, 9, 12) and _day(y, m, 20) <= now
        _pay_obligations(db, ctx, state, d, prev_end, vat_due=vat_due)
    # Day 12: retail sale paid in cash
    d = _day(y, m, 12)
    if ok(d):
        _sale(
            db,
            ctx,
            state,
            d,
            None,
            [
                _item_line(state, "keyboard", rnd.randint(1, 2)),
                _item_line(state, "monitor", 1),
            ],
            method="cash",
            description="Bán lẻ thu tiền mặt",
        )
    # Day 15: collect customer payments by bank transfer
    d = _day(y, m, 15)
    if ok(d):
        for kh in (kh1, kh2, kh3):
            due = queries.balance(
                db, cid, "131", exact=True, upto=d, partner_id=kh.id
            )
            if due > 0:
                _money(
                    db,
                    ctx,
                    "BC",
                    d,
                    [
                        (
                            "131",
                            _round_k(due * rnd.choice([0.8, 0.9, 1.0])),
                            "Thu tiền hàng",
                            kh.id,
                        )
                    ],
                    f"{kh.name} thanh toán tiền hàng",
                    bank=state.vcb,
                    partner=kh,
                )
    # Day 16: a smaller customer pays part of its debt in cash; scrap
    # is sold at quarter end
    d = _day(y, m, 16)
    if ok(d):
        due = queries.balance(
            db, cid, "131", exact=True, upto=d, partner_id=kh2.id
        )
        if due > 0:
            _money(
                db,
                ctx,
                "PT",
                d,
                [
                    (
                        "131",
                        min(due, 8_000_000),
                        "Thu tiền hàng bằng tiền mặt",
                        kh2.id,
                    )
                ],
                f"{kh2.name} nộp tiền mặt thanh toán công nợ",
                partner=kh2,
                contact="Nguyễn Thị Lan",
            )
        if m % 3 == 0:
            _money(
                db,
                ctx,
                "PT",
                d,
                [("711", 1_500_000, "Thanh lý thùng carton, phế liệu", None)],
                "Thu tiền thanh lý phế liệu",
                contact="Cơ sở thu mua phế liệu Hưng Thịnh",
            )
    # Day 18: electricity and office expenses (cash, with VAT invoice)
    d = _day(y, m, 18)
    if ok(d):
        goods = rnd.randint(25, 40) * 100_000
        _money(
            db,
            ctx,
            "PC",
            d,
            [("6422", goods, f"Tiền điện tháng {m:02d}/{y}", None)],
            f"Thanh toán tiền điện tháng {m:02d}/{y}",
            contact="Tổng công ty Điện lực TP. Hồ Chí Minh",
            vat=VatInfo(
                goods_amount=goods,
                vat_rate="10",
                vat_amount=goods // 10,
                invoice_series=f"K{y % 100}TEV",
                invoice_no=_invoice(state, "EVN"),
                invoice_template="1",
                invoice_date=d,
                partner_name="Tổng công ty Điện lực TP. Hồ Chí Minh",
                partner_tax_code="0300951119",
            ),
        )
        _money(
            db,
            ctx,
            "PC",
            d,
            [
                (
                    "6421",
                    rnd.randint(10, 30) * 100_000,
                    "Chi phí tiếp khách, xăng xe",
                    None,
                )
            ],
            "Chi tiếp khách, xăng xe đi gặp khách hàng",
            contact="Lê Minh Quân",
        )
    # Day 20: training service for the college (8% VAT, paid by bank)
    d = _day(y, m, 20)
    if ok(d) and m % 3 == 1:
        _sale(
            db,
            ctx,
            state,
            d,
            kh3,
            [_item_line(state, "training", 1, 18_000_000)],
            method="bank",
            description="Đào tạo chuyển đổi số cho giảng viên",
        )
    # Day 22: pay suppliers
    d = _day(y, m, 22)
    if ok(d):
        due = _credit_balance(db, cid, "331", d, partner_id=ncc_tb.id)
        if due > 0:
            _money(
                db,
                ctx,
                "BN",
                d,
                [
                    (
                        "331",
                        _round_k(due * 0.9),
                        "Thanh toán tiền hàng",
                        ncc_tb.id,
                    )
                ],
                f"Thanh toán cho {ncc_tb.name}",
                bank=state.vcb,
                partner=ncc_tb,
            )
        due = _credit_balance(db, cid, "331", d, partner_id=ncc_nt.id)
        if due > 0:
            _money(
                db,
                ctx,
                "BN",
                d,
                [("331", due, "Thanh toán tiền hàng", ncc_nt.id)],
                f"Thanh toán cho {ncc_nt.name}",
                bank=state.vcb,
                partner=ncc_nt,
            )
    # Day 25: office rent (paid from TCB, with VAT invoice)
    d = _day(y, m, 25)
    if ok(d):
        _money(
            db,
            ctx,
            "BN",
            d,
            [
                (
                    "6422",
                    12_000_000,
                    f"Tiền thuê văn phòng tháng {m:02d}/{y}",
                    None,
                )
            ],
            f"Thanh toán tiền thuê văn phòng tháng {m:02d}/{y}",
            bank=state.tcb,
            partner=ncc_bds,
            vat=VatInfo(
                goods_amount=12_000_000,
                vat_rate="10",
                vat_amount=1_200_000,
                invoice_series=f"C{y % 100}TBD",
                invoice_no=_invoice(state, "BDS"),
                invoice_template="1",
                invoice_date=d,
                partner_name=ncc_bds.name,
                partner_tax_code=ncc_bds.tax_code,
            ),
        )


def _one_offs(
    db: Session, ctx, state: _Ctx, y0: int, y: int, m: int, now: date
) -> None:
    """One-off transactions.

    Buys a fixed asset and office furniture (tools), records a sales
    return, etc.
    """
    from app.features.assets import service as assets
    from app.features.sales.models import RETURN

    _ncc_tb, ncc_nt, _ = state.suppliers
    if (y, m) == (y0, 2):
        d = _day(y, m, 10)
        e = _purchase(
            db,
            ctx,
            state,
            d,
            state.suppliers[0],
            [
                {
                    "description": "Máy chủ Dell PowerEdge R650",
                    "quantity": 1,
                    "unit_price": 96_000_000,
                    "vat_rate": "10",
                    "account": "2111",
                }
            ],
            description="Mua máy chủ phục vụ triển khai phần mềm",
        )
        assets.create_asset(
            db,
            ctx,
            assets.AssetInput(
                name="Máy chủ Dell PowerEdge R650",
                cost=96_000_000,
                useful_life_months=48,
                acquired_date=d,
                category="machine",
                asset_account="2111",
                expense_account="6422",
                department="Kỹ thuật",
                manufacturer="Dell – Mỹ",
                source="purchase",
                entry_id=e.id,
                partner_id=state.suppliers[0].id,
            ),
        )
    if (y, m) == (y0, 3):
        d = _day(y, m, 5)
        e = _purchase(
            db,
            ctx,
            state,
            d,
            ncc_nt,
            [
                {
                    "description": "Bàn ghế làm việc, tủ hồ sơ văn phòng",
                    "quantity": 1,
                    "unit_price": 36_000_000,
                    "vat_rate": "10",
                    "account": "242",
                }
            ],
            description="Mua nội thất văn phòng",
        )
        assets.create_prepaid(
            db,
            ctx,
            assets.PrepaidInput(
                name="Bàn ghế làm việc, tủ hồ sơ văn phòng",
                amount=36_000_000,
                months=24,
                start_month=d,
                kind="tool",
                quantity=1,
                expense_account="6422",
                department="Văn phòng",
                entry_id=e.id,
            ),
        )
    if (y, m) == (y0, 6) and state.sales_for_return is not None:
        d = _day(y, m, 9)
        if d <= now:
            _sale(
                db,
                ctx,
                state,
                d,
                db.get(
                    type(state.customers[0]), state.sales_for_return.partner_id
                ),
                [_item_line(state, "laptop", 1)],
                kind=RETURN,
                original_entry_id=state.sales_for_return.entry_id,
                description=(
                    "Khách hàng trả lại 1 máy tính xách tay lỗi màn hình"
                ),
            )


def _year_end(db: Session, ctx, company: Company, year: int) -> None:
    """Close the year.

    Records corporate income tax, runs the closing entries, carries the
    profit forward to the next year and locks the year.
    """
    from app.features.gl import closing, periods, statements
    from app.features.journal import locks

    period = periods.make("year", year)
    st = statements.b02(db, company, period)
    revenue, pretax = st.value("10"), st.value("50")
    rate = (
        15
        if revenue <= 3_000_000_000
        else 17
        if revenue <= 50_000_000_000
        else 20
    )
    cit = int(Decimal(pretax * rate) / 100) if pretax > 0 else 0
    if cit > 0:
        closing.record_cit(
            db,
            ctx,
            date(year, 12, 31),
            cit,
            f"Chi phí thuế TNDN năm {year} (thuế suất {rate}%)",
        )
    closing.run(db, ctx, period)
    closing.run_retained(db, ctx, year)
    locks.lock_period(db, ctx, year, 0)


# ---------------------------------------------------- API


def build_demo(db: Session, ctx, *, year: int | None = None) -> dict:
    """Create sample data for the company of ``ctx``.

    The company must not have any posted documents yet. Returns a
    summary of the document counts.
    """
    from app.features.cash.counts import CashCountInput, create_cash_count
    from app.features.cash.models import DENOMINATIONS
    from app.features.org.service import set_start_date

    now = today()
    y0 = year or now.year - 1
    start = date(y0, 1, 1)
    company = db.get(Company, ctx.company_id)
    if company.start_date != start:
        set_start_date(db, ctx, start)
        company = db.get(Company, ctx.company_id)
    rnd = random.Random(20260101 + company.id)
    state = _Ctx()
    _catalog(db, ctx, state, start)
    _opening(db, ctx, state)

    y, m = y0, 1
    while (y, m) <= (now.year, now.month):
        _one_offs(db, ctx, state, y0, y, m, now)
        _month(db, ctx, state, company, rnd, y, m, now)
        end = month_bounds(y, m)[1]
        if end < now:
            _month_end_runs(db, ctx, state, y, m)
            if m in (3, 6, 9, 12):
                _quarter_vat(db, ctx, company, end)
            if m == 12:
                _year_end(db, ctx, company, y)
        # Pay last year's corporate income tax in Q1
        if (y, m) == (y0 + 1, 3) and _day(y, m, 25) <= now:
            cit = _credit_balance(db, ctx.company_id, "3334", _day(y, m, 24))
            if cit > 0:
                _money(
                    db,
                    ctx,
                    "BN",
                    _day(y, m, 25),
                    [("3334", cit, f"Nộp thuế TNDN năm {y0}", None)],
                    f"Nộp thuế TNDN năm {y0}",
                    bank=state.vcb,
                    contact="Kho bạc Nhà nước TP. Hồ Chí Minh",
                )
        m += 1
        if m > 12:
            y, m = y + 1, 1

    # Draft documents still in progress
    kh = state.customers[1]
    _sale(
        db,
        ctx,
        state,
        now,
        kh,
        [_item_line(state, "monitor", 2)],
        post=False,
        description="Báo giá đã chốt – chờ xuất hóa đơn",
    )
    _money(
        db,
        ctx,
        "PC",
        now,
        [("141", 5_000_000, "Tạm ứng công tác Hà Nội", None)],
        "Tạm ứng đi công tác Hà Nội",
        contact="Phạm Thu Trang",
        post=False,
    )

    # Cash count today: physical cash matches the cash book
    cash = queries.balance(db, ctx.company_id, "1111", exact=True, upto=now)
    rest, qty = cash, {}
    for den in DENOMINATIONS:
        qty[den], rest = divmod(rest, den)
    if rest == 0:
        create_cash_count(
            db,
            ctx,
            CashCountInput(
                count_date=now,
                count_time="16:30",
                quantities=qty,
                cashier_name=company.cashier or "Thủ quỹ",
                accountant_name=company.chief_accountant or "Kế toán",
                conclusion="Tiền mặt thực tế khớp sổ quỹ",
            ),
        )
    rows = db.execute(
        select(
            JournalEntry.doc_type,
            JournalEntry.status,
            func.count(JournalEntry.id),
        )
        .where(JournalEntry.company_id == ctx.company_id)
        .group_by(JournalEntry.doc_type, JournalEntry.status)
    ).all()
    summary: dict = {"start": start, "end": now, "entries": {}}
    for doc_type, status, n in rows:
        summary["entries"].setdefault(doc_type, {})[status] = n
    summary["total"] = sum(n for *_x, n in rows)
    return summary
