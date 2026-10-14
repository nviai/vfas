from app.core.registry import NavItem

NAV = [
    NavItem(
        section="system",
        label="API & tích hợp",
        href="/settings/api-tokens",
        icon="key",
        perm="api.manage",
        order=60,
    )
]
