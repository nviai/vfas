from app.core.registry import ACCOUNTANTS, READERS

PERMISSIONS = {
    "vat.view": READERS,
    "vat.export": READERS,
    "vat.manage": ACCOUNTANTS,
}
