from app.core.registry import ACCOUNTANTS, READERS

PERMISSIONS = {
    "bank.view": READERS,
    "bank.export": READERS,
    "bank.create": ACCOUNTANTS,
    "bank.post": ACCOUNTANTS,
    "bank.cancel": ACCOUNTANTS,
    "bank.reconcile": ACCOUNTANTS,
    "bank.manage": ACCOUNTANTS,  # bank account list, suggestion rules
}
