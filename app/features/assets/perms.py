from app.core.registry import ACCOUNTANTS, READERS

PERMISSIONS = {
    "assets.view": READERS,
    "assets.export": READERS,
    "assets.create": ACCOUNTANTS,
    "assets.post": ACCOUNTANTS,
    "assets.cancel": ACCOUNTANTS,
    "assets.manage": ACCOUNTANTS,
}
