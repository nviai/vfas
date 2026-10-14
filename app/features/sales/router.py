"""Sales (BH) and sales returns / allowances (TL)."""

from __future__ import annotations

from fastapi import APIRouter

from app.features.sales import service
from app.features.sales.trade_web import TradeConfig, register

router = APIRouter()

CONFIG = TradeConfig(
    module="sales",
    base="/sales",
    service=service,
    main_kind="sale",
    titles={
        "sale": "Chứng từ bán hàng",
        "return": "Giảm giá, trả lại hàng bán",
    },
    list_titles={"sale": "Bán hàng", "return": "Giảm giá, trả lại hàng bán"},
    subtitles={
        "sale": "Doanh thu, thuế GTGT đầu ra và công nợ phải thu",
        "return": "Điều chỉnh giảm doanh thu, thuế GTGT đầu ra",
    },
    partner_label="Khách hàng",
    partner_role="customer",
    price_field="sale_price",
    account_params={"postable": 1, "prefix": "511,515,711"},
    account_label="TK doanh thu",
    show_discount=True,
    deductible=False,
    payment_labels={
        "credit": "Ghi nợ (TK 131)",
        "cash": "Tiền mặt (TK 1111)",
        "bank": "Chuyển khoản (TK 1121)",
    },
    section="Bán hàng",
    icon="shopping-cart",
    account_attr="revenue_account",
    item_account_expr=(
        "(($event.detail.extra || {}).revenue_account) || line.account"
    ),
)
register(router, CONFIG)
