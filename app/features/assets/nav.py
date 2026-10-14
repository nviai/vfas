from app.core.registry import DocType, NavItem, QuickAction

NAV = [
    NavItem(
        section="assets",
        label="Tài sản cố định",
        href="/assets",
        icon="building",
        perm="assets.view",
        order=10,
    ),
    NavItem(
        section="assets",
        label="Khấu hao TSCĐ",
        href="/assets/depreciation",
        icon="trending-down",
        perm="assets.view",
        order=20,
    ),
    NavItem(
        section="assets",
        label="CCDC & chi phí trả trước",
        href="/prepaid",
        icon="package",
        perm="assets.view",
        order=30,
    ),
    NavItem(
        section="assets",
        label="Phân bổ chi phí",
        href="/prepaid/allocation",
        icon="calendar",
        perm="assets.view",
        order=40,
    ),
]
ACTIONS = [
    QuickAction(
        label="Ghi tăng TSCĐ",
        href="/assets/new",
        icon="building",
        perm="assets.create",
        group="Tài sản",
        order=60,
    ),
    QuickAction(
        label="CCDC / chi phí trả trước",
        href="/prepaid/new",
        icon="package",
        perm="assets.create",
        group="Tài sản",
        order=61,
    ),
]
DOC_TYPES = [
    DocType("KH", "Khấu hao TSCĐ", url="/assets/entries/{id}", perm="assets"),
    DocType("TS", "Ghi giảm TSCĐ", url="/assets/entries/{id}", perm="assets"),
    DocType(
        "PB", "Phân bổ chi phí", url="/assets/entries/{id}", perm="assets"
    ),
]
