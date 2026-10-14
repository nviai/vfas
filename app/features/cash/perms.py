from app.core.registry import (
    ACCOUNTANTS,
    ALL_ROLES,
    BOOKKEEPERS,
    CASHIER,
    READERS,
)

PERMISSIONS = {
    "cash.view": ALL_ROLES,
    "cash.export": READERS | {CASHIER},
    "cash.create": BOOKKEEPERS,
    "cash.post": BOOKKEEPERS,
    "cash.cancel": ACCOUNTANTS,
    # Receipt/payment purpose list
    "cash.manage": ACCOUNTANTS,
    "cashcount.manage": BOOKKEEPERS,
}
