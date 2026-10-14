"""Payroll permissions.

Payroll data is sensitive: only the business owner, the chief
accountant and accountants can access it (cashiers and view-only users
cannot).
"""

from app.core.registry import ACCOUNTANTS

PERMISSIONS = {
    "payroll.view": ACCOUNTANTS,
    "payroll.export": ACCOUNTANTS,
    "payroll.create": ACCOUNTANTS,
    "payroll.post": ACCOUNTANTS,
    "payroll.cancel": ACCOUNTANTS,
    "payroll.manage": ACCOUNTANTS,
}
