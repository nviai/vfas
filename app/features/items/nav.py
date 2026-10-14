from app.core.registry import NavItem

NAV = [
    NavItem(
        section="catalog",
        label="Hàng hóa, dịch vụ",
        href="/items",
        icon="package",
        perm="catalog.view",
        order=30,
    )
]
