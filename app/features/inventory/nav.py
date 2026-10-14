from app.core.registry import DocType, NavItem, QuickAction

NAV = [
    NavItem(
        section="inventory",
        label="Nhập kho",
        href="/inventory/receipts",
        icon="arrow-down-circle",
        perm="inventory.view",
        order=10,
    ),
    NavItem(
        section="inventory",
        label="Xuất kho",
        href="/inventory/issues",
        icon="arrow-up-circle",
        perm="inventory.view",
        order=20,
    ),
    NavItem(
        section="inventory",
        label="Chuyển kho",
        href="/inventory/transfers",
        icon="arrow-left-right",
        perm="inventory.view",
        order=30,
    ),
    NavItem(
        section="inventory",
        label="Tính giá xuất kho",
        href="/inventory/costing",
        icon="calculator",
        perm="inventory.view",
        order=40,
    ),
    NavItem(
        section="inventory",
        label="Thẻ kho",
        href="/inventory/stock-card",
        icon="clipboard-list",
        perm="inventory.view",
        order=50,
    ),
    NavItem(
        section="inventory",
        label="Sổ chi tiết hàng hóa",
        href="/inventory/ledger",
        icon="book-open",
        perm="inventory.view",
        order=55,
    ),
    NavItem(
        section="inventory",
        label="Tổng hợp nhập – xuất – tồn",
        href="/inventory/summary",
        icon="boxes",
        perm="inventory.view",
        order=60,
    ),
    NavItem(
        section="catalog",
        label="Kho",
        href="/inventory/warehouses",
        icon="warehouse",
        perm="inventory.view",
        order=40,
    ),
    NavItem(
        section="catalog",
        label="Tồn kho đầu kỳ",
        href="/inventory/opening",
        icon="archive",
        perm="inventory.view",
        order=45,
    ),
]
ACTIONS = [
    QuickAction(
        label="Phiếu nhập kho",
        href="/inventory/receipts/new",
        icon="arrow-down-circle",
        perm="inventory.create",
        group="Kho",
        order=60,
    ),
    QuickAction(
        label="Phiếu xuất kho",
        href="/inventory/issues/new",
        icon="arrow-up-circle",
        perm="inventory.create",
        group="Kho",
        order=61,
    ),
]
DOC_TYPES = [
    DocType(
        "NK", "Nhập kho", url="/inventory/receipts/{id}", perm="inventory"
    ),
    DocType(
        "GV",
        "Giá vốn hàng xuất",
        url="/inventory/costing/{id}",
        perm="inventory",
    ),
]
