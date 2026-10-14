from app.core.registry import DocType, NavItem, QuickAction

NAV = [
    NavItem(
        section="money",
        label="Phiếu thu, chi",
        href="/cash",
        icon="wallet",
        perm="cash.view",
        order=10,
    ),
    NavItem(
        section="money",
        label="Sổ quỹ tiền mặt",
        href="/cash/book",
        icon="book-open",
        perm="cash.view",
        order=20,
    ),
    NavItem(
        section="money",
        label="Kiểm kê quỹ",
        href="/cash/counts",
        icon="calculator",
        perm="cash.view",
        order=30,
    ),
]
ACTIONS = [
    QuickAction(
        label="Phiếu thu",
        href="/cash/new?kind=PT",
        icon="circle-arrow-down",
        perm="cash.create",
        shortcut="Alt+T",
        order=10,
    ),
    QuickAction(
        label="Phiếu chi",
        href="/cash/new?kind=PC",
        icon="circle-arrow-up",
        perm="cash.create",
        shortcut="Alt+C",
        order=11,
    ),
]
DOC_TYPES = [
    DocType("PT", "Phiếu thu", url="/cash/{id}", perm="cash"),
    DocType("PC", "Phiếu chi", url="/cash/{id}", perm="cash"),
]
