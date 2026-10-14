from app.core.registry import DocType, NavItem, QuickAction

NAV = [
    NavItem(
        section="purchases",
        label="Mua hàng",
        href="/purchases",
        icon="truck",
        perm="purchases.view",
        order=10,
    ),
    NavItem(
        section="purchases",
        label="Trả lại hàng mua",
        href="/purchases/returns",
        icon="undo-2",
        perm="purchases.view",
        order=15,
    ),
    NavItem(
        section="purchases",
        label="Nhập hóa đơn điện tử",
        href="/einvoices",
        icon="file-up",
        perm="purchases.create",
        order=20,
    ),
]
ACTIONS = [
    QuickAction(
        label="Chứng từ mua hàng",
        href="/purchases/new",
        icon="truck",
        perm="purchases.create",
        order=31,
    ),
    QuickAction(
        label="Nhập hóa đơn XML",
        href="/einvoices",
        icon="file-up",
        perm="purchases.create",
        order=32,
    ),
]
DOC_TYPES = [
    DocType("MH", "Mua hàng", url="/purchases/{id}", perm="purchases"),
    DocType("TM", "Trả lại hàng mua", url="/purchases/{id}", perm="purchases"),
]
