"""Core permissions mapped to their default roles.

Feature modules declare additional permissions in their own perms.py.
"""

from app.core.registry import ACCOUNTANTS, ALL_ROLES, MANAGERS, READERS

PERMISSIONS: dict[str, frozenset[str]] = {
    "company.view": ALL_ROLES,
    "dashboard.view": ALL_ROLES,
    "settings.manage": MANAGERS,
    "members.manage": MANAGERS,
    "audit.view": READERS,
    "checks.view": READERS,
    # Catalogs
    "catalog.view": ALL_ROLES,
    "catalog.create": ACCOUNTANTS,
    "catalog.manage": ACCOUNTANTS,
    # General vouchers, general ledger
    "journal.view": READERS,
    "journal.export": READERS,
    "journal.create": ACCOUNTANTS,
    "journal.post": ACCOUNTANTS,
    "journal.cancel": ACCOUNTANTS,
    "journal.reverse": ACCOUNTANTS,
    # Edit the description of a posted voucher
    "journal.edit_posted": ACCOUNTANTS,
    "opening.manage": MANAGERS,
    "locks.manage": MANAGERS,
    "reports.view": READERS,
    "reports.export": READERS,
    "import.manage": MANAGERS,
    # AI, API
    "ai.use": ALL_ROLES,
    "ai.manage": MANAGERS,
    "api.manage": MANAGERS,
}
