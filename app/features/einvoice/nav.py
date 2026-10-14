from app.core.registry import NavItem

NAV = [
    NavItem(
        section="sales",
        label="Hóa đơn điện tử",
        href="/einvoice",
        icon="receipt",
        perm="einvoice.view",
        order=40,
    ),
]
