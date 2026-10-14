from app.core.registry import DocType, NavItem, QuickAction

NAV = [
    NavItem(
        section="books",
        label="Chứng từ",
        href="/entries",
        icon="files",
        perm="journal.view",
        order=5,
    ),
    NavItem(
        section="books",
        label="Sổ Nhật ký chung",
        href="/reports/s03a",
        icon="notebook-pen",
        perm="reports.view",
        order=20,
    ),
    NavItem(
        section="books",
        label="Sổ Cái",
        href="/reports/s03b",
        icon="book-open",
        perm="reports.view",
        order=21,
    ),
    NavItem(
        section="books",
        label="Sổ chi tiết tài khoản",
        href="/reports/ledger",
        icon="book-text",
        perm="reports.view",
        order=22,
    ),
    NavItem(
        section="books",
        label="Bảng cân đối tài khoản",
        href="/reports/f01",
        icon="scale",
        perm="reports.view",
        order=30,
    ),
    NavItem(
        section="books",
        label="Báo cáo tài chính",
        href="/reports",
        icon="chart-column",
        perm="reports.view",
        order=40,
    ),
    NavItem(
        section="books",
        label="Kết chuyển cuối kỳ",
        href="/closing",
        icon="repeat",
        perm="closing.run",
        order=60,
    ),
    NavItem(
        section="books",
        label="Khóa sổ",
        href="/locks",
        icon="lock",
        perm="journal.view",
        order=70,
    ),
    NavItem(
        section="catalog",
        label="Số dư đầu kỳ",
        href="/opening",
        icon="scroll-text",
        perm="journal.view",
        order=30,
    ),
]

ACTIONS = [
    QuickAction(
        label="Phiếu kế toán",
        href="/entries/new",
        icon="square-pen",
        perm="journal.create",
        order=50,
    ),
]

DOC_TYPES = [
    DocType("PK", "Phiếu kế toán", url="/entries/{id}", perm="journal"),
    DocType("KC", "Kết chuyển", url="/entries/{id}", perm="journal"),
]
