from app.core.registry import DocType, NavItem, QuickAction

NAV = [
    NavItem(
        section="payroll",
        label="Nhân viên",
        href="/payroll/employees",
        icon="users",
        perm="payroll.view",
        order=10,
    ),
    NavItem(
        section="payroll",
        label="Bảng lương",
        href="/payroll",
        icon="banknote",
        perm="payroll.view",
        order=20,
        match="/payroll/runs",
    ),
    NavItem(
        section="payroll",
        label="Báo cáo lương, BH, TNCN",
        href="/payroll/reports",
        icon="file-spreadsheet",
        perm="payroll.view",
        order=30,
    ),
    NavItem(
        section="payroll",
        label="Tham số lương & thuế",
        href="/payroll/settings",
        icon="settings",
        perm="payroll.view",
        order=40,
    ),
]
ACTIONS = [
    QuickAction(
        label="Bảng lương tháng",
        href="/payroll",
        icon="banknote",
        perm="payroll.create",
        group="Lương",
        order=70,
    ),
]
DOC_TYPES = [
    DocType(
        "LG", "Hạch toán lương", url="/payroll/entries/{id}", perm="payroll"
    )
]
