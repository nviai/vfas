from app.core.registry import NavItem

NAV = [
    NavItem(
        section="sales",
        label="Công nợ phải thu",
        href="/debts/receivables",
        icon="hand-coins",
        perm="debts.view",
        order=30,
    ),
    NavItem(
        section="purchases",
        label="Công nợ phải trả",
        href="/debts/payables",
        icon="coins",
        perm="debts.view",
        order=30,
    ),
]
