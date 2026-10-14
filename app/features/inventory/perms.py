from app.core.registry import ACCOUNTANTS, READERS

PERMISSIONS = {
    "inventory.view": READERS,
    "inventory.export": READERS,
    "inventory.create": ACCOUNTANTS,
    "inventory.post": ACCOUNTANTS,
    "inventory.cancel": ACCOUNTANTS,
    "inventory.costing": ACCOUNTANTS,
    "inventory.manage": ACCOUNTANTS,
}
