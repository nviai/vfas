from app.core.registry import NavItem

NAV = [
    NavItem(
        section="overview",
        label="Tổng quan",
        href="/",
        icon="layout-dashboard",
        perm="dashboard.view",
        order=1,
    )
]
