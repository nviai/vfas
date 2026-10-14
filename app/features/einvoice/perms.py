from app.core.registry import ACCOUNTANTS, MANAGERS, READERS

PERMISSIONS = {
    "einvoice.view": READERS,
    "einvoice.export": READERS,
    # Issue invoices / refresh their status.
    "einvoice.issue": ACCOUNTANTS,
    # Provider account and series.
    "einvoice.manage": MANAGERS,
}
