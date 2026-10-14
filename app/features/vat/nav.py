from app.core.registry import NavItem

NAV = [
    NavItem(
        section="tax",
        label="Bảng kê thuế GTGT",
        href="/vat/lists",
        icon="list",
        perm="vat.view",
        order=10,
    ),
    NavItem(
        section="tax",
        label="Tờ khai 01/GTGT",
        href="/vat/declarations",
        icon="file-text",
        perm="vat.view",
        order=20,
    ),
    NavItem(
        section="tax",
        label="Quyết toán TNDN",
        href="/vat/cit",
        icon="landmark",
        perm="vat.view",
        order=30,
    ),
    NavItem(
        section="tax",
        label="Lịch nộp thuế",
        href="/vat/calendar",
        icon="calendar",
        perm="vat.view",
        order=40,
    ),
]
