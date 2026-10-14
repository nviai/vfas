from app.core.registry import DocType, NavItem, QuickAction

NAV = [
    NavItem(
        section="sales",
        label="Bán hàng",
        href="/sales",
        icon="shopping-cart",
        perm="sales.view",
        order=10,
    ),
    NavItem(
        section="sales",
        label="Giảm giá, trả lại",
        href="/sales/returns",
        icon="undo-2",
        perm="sales.view",
        order=20,
    ),
]
ACTIONS = [
    QuickAction(
        label="Chứng từ bán hàng",
        href="/sales/new",
        icon="shopping-cart",
        perm="sales.create",
        order=30,
    )
]
DOC_TYPES = [
    DocType("BH", "Bán hàng", url="/sales/{id}", perm="sales"),
    DocType(
        "TL", "Giảm giá, trả lại hàng bán", url="/sales/{id}", perm="sales"
    ),
]
