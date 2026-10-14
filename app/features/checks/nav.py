from app.core.registry import NavItem

NAV = [
    NavItem(
        section="books",
        label="Kiểm tra dữ liệu",
        href="/checks",
        icon="shield-check",
        perm="checks.view",
        order=90,
    )
]
