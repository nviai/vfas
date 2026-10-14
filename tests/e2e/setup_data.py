"""Prepare E2E data after `python -m app.cli demo`.

Runs in a subprocess (DATA_DIR already set): adds members for every
role, another company (isolation checks), configures the AI assistant
to use the fake LLM and creates a draft sale for the e-invoice test.
Prints the info as JSON to stdout.
Usage::

    python tests/e2e/setup_data.py <llm_base_url>
"""

from __future__ import annotations

import json
import sys
from datetime import date


def main(llm_url: str) -> None:
    from sqlalchemy import select

    from app.core.config import get_settings
    from app.core.db import configure_engine, session_scope
    from app.core.registry import load_features
    from app.core.security import build_current_user
    from app.core.tenancy import Ctx
    from app.features.ai.config import save_config
    from app.features.auth.service import (
        create_user,
        get_user_by_email,
        set_password,
    )
    from app.features.org.models import Company
    from app.features.org.service import (
        CompanyInput,
        add_member_direct,
        create_company,
    )

    configure_engine(get_settings().db_url)
    load_features()
    pw = "Ketoan#2026"
    out: dict = {"password": pw, "users": {}}
    with session_scope() as db:
        company = db.scalars(select(Company).order_by(Company.id)).first()
        owner = get_user_by_email(db, "demo@example.com")
        # every E2E account shares one password
        set_password(db, owner, pw)
        out["company_id"] = company.id
        out["company_tax_code"] = company.tax_code
        out["users"]["owner"] = "demo@example.com"
        for role, email in (
            ("chief_accountant", "ktt@e2e.vn"),
            ("accountant", "ketoan@e2e.vn"),
            ("cashier", "thuquy@e2e.vn"),
            ("viewer", "chixem@e2e.vn"),
        ):
            u = create_user(db, email, pw, f"E2E {role}", commit=False)
            db.flush()
            add_member_direct(db, company.id, u, role)
            out["users"][role] = email
        other = create_user(
            db, "khac@e2e.vn", pw, "Chủ công ty khác", commit=False
        )
        db.flush()
        c2 = create_company(
            db,
            other,
            CompanyInput(
                name="CÔNG TY KHÁC (E2E)",
                tax_code="0109999999",
                start_date=date(date.today().year, 1, 1),
            ),
        )
        out["other_company_id"] = c2.id
        out["users"]["other"] = "khac@e2e.vn"
        ctx = Ctx(
            company_id=company.id,
            user=build_current_user(db, owner, company.id),
        )
        save_config(
            db,
            ctx,
            {
                "enabled": True,
                "preset": "custom",
                "base_url": llm_url,
                "model": "fake-e2e",
                "api_key": "sk-e2e-fake-key",
                "temperature": 0.1,
                "max_rounds": 4,
                "daily_limit": 1000,
            },
        )
        # Draft sale dated today, to be issued as an e-invoice
        from decimal import Decimal

        from app.core.dates import today
        from app.features.partners.models import Partner
        from app.features.sales import service as sales
        from app.features.sales.common import TradeHeader, TradeLine

        customer = db.scalars(
            select(Partner).where(
                Partner.company_id == company.id,
                Partner.is_customer.is_(True),
                Partner.tax_code != "",
            )
        ).first()
        d = today()
        doc = sales.create(
            db,
            ctx,
            sales.SalesInput(
                kind="sale",
                header=TradeHeader(
                    date=d,
                    doc_date=d,
                    partner_id=customer.id,
                    partner_name="",
                    address="",
                    tax_code="",
                    contact_name="",
                    invoice_template="",
                    invoice_series="",
                    invoice_no="",
                    invoice_date=None,
                    payment_method="credit",
                    bank_account_id=None,
                    due_date=None,
                    description="Bán hàng E2E – hóa đơn điện tử",
                    note="",
                    original_entry_id=None,
                ),
                lines=[
                    TradeLine(
                        description="Dịch vụ tư vấn E2E",
                        amount=5_000_000,
                        vat_rate="10",
                        vat_amount=500_000,
                        quantity=Decimal("1"),
                        unit_price=Decimal("5000000"),
                        account="5113",
                    )
                ],
            ),
        )
        out["einvoice_entry_id"] = doc.entry_id
    print(json.dumps(out))


if __name__ == "__main__":
    main(sys.argv[1])
