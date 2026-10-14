from app.core.registry import DocType, NavItem, QuickAction

NAV = [
    NavItem(
        section="money",
        label="Tiền gửi ngân hàng",
        href="/bank",
        icon="landmark",
        perm="bank.view",
        order=40,
    ),
    NavItem(
        section="money",
        label="Sổ tiền gửi",
        href="/bank/book",
        icon="book-text",
        perm="bank.view",
        order=50,
    ),
    NavItem(
        section="money",
        label="Đối chiếu sao kê",
        href="/bank/statements",
        icon="git-compare",
        perm="bank.view",
        order=60,
    ),
    NavItem(
        section="catalog",
        label="Tài khoản ngân hàng",
        href="/bank-accounts",
        icon="landmark",
        perm="bank.view",
        order=30,
    ),
]
ACTIONS = [
    QuickAction(
        label="Thu tiền gửi",
        href="/bank/new?kind=BC",
        icon="arrow-down-left",
        perm="bank.create",
        order=20,
    ),
    QuickAction(
        label="Chi tiền gửi",
        href="/bank/new?kind=BN",
        icon="arrow-up-right",
        perm="bank.create",
        order=21,
    ),
    QuickAction(
        label="Chuyển tiền nội bộ",
        href="/bank/transfer/new",
        icon="arrow-left-right",
        perm="bank.create",
        order=22,
    ),
]
DOC_TYPES = [
    DocType("BC", "Thu tiền gửi", url="/bank/{id}", perm="bank"),
    DocType("BN", "Chi tiền gửi", url="/bank/{id}", perm="bank"),
    DocType("CK", "Chuyển tiền nội bộ", url="/bank/{id}", perm="bank"),
]
