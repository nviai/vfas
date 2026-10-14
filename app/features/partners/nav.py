from app.core.registry import NavItem, QuickAction

NAV = [
    NavItem(
        section="catalog",
        label="Khách hàng, nhà cung cấp",
        href="/partners",
        icon="users",
        perm="catalog.view",
        order=20,
    )
]
ACTIONS = [
    QuickAction(
        label="Đối tượng mới",
        href="/partners/new",
        icon="user-plus",
        perm="catalog.create",
        group="Danh mục",
        order=90,
    )
]
