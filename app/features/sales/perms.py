from app.core.registry import ACCOUNTANTS, READERS

PERMISSIONS = {
    "sales.view": READERS,
    "sales.export": READERS,
    "sales.create": ACCOUNTANTS,
    "sales.post": ACCOUNTANTS,
    "sales.cancel": ACCOUNTANTS,
}
