from app.core.registry import ACCOUNTANTS, READERS

PERMISSIONS = {
    "purchases.view": READERS,
    "purchases.export": READERS,
    "purchases.create": ACCOUNTANTS,
    "purchases.post": ACCOUNTANTS,
    "purchases.cancel": ACCOUNTANTS,
}
