from app.core.registry import NavItem, QuickAction

NAV = [
    NavItem(
        section="overview",
        label="Trợ lý AI",
        href="/ai",
        icon="sparkles",
        perm="ai.use",
        order=5,
    ),
    NavItem(
        section="system",
        label="Cài đặt trợ lý AI",
        href="/settings/ai",
        icon="bot",
        perm="ai.manage",
        order=50,
    ),
]
ACTIONS = [
    QuickAction(
        label="Hỏi trợ lý AI",
        href="/ai",
        icon="sparkles",
        perm="ai.use",
        shortcut="Ctrl+J",
        group="Trợ lý",
        order=95,
    )
]
