from app.core.registry import NavItem

NAV = [
    NavItem(
        section="system",
        label="Nhật ký thao tác",
        href="/audit",
        icon="history",
        perm="audit.view",
        order=40,
    )
]
