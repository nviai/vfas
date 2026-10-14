from app.core.registry import NavItem

NAV = [
    NavItem(
        section="system",
        label="Thông tin công ty",
        href="/settings",
        icon="building",
        perm="settings.manage",
        order=10,
    ),
    NavItem(
        section="system",
        label="Thành viên",
        href="/members",
        icon="users",
        perm="members.manage",
        order=20,
    ),
]
