from app.core.registry import NavItem

NAV = [
    NavItem(
        section="catalog",
        label="Hệ thống tài khoản",
        href="/accounts",
        icon="book-open",
        perm="catalog.view",
        order=10,
    )
]
