"""Feature registry.

Holds navigation, permissions, quick actions, document types, AI tools,
data checks and hooks.

Each feature declares them in optional files (loaded automatically by
`load_features()`):
  nav.py       NAV = [NavItem(...)], ACTIONS = [QuickAction(...)],
               DOC_TYPES = [DocType(...)]
  perms.py     PERMISSIONS = {"cash.view": ALL_ROLES, ...}
  ai_tools.py  functions decorated with @ai_tool(...)
  checks.py    functions decorated with @data_check(...)
  hooks.py     functions decorated with @posting_validator /
               @on("entry.posted")
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------- roles

OWNER = "owner"
CHIEF = "chief_accountant"
ACCOUNTANT = "accountant"
CASHIER = "cashier"
VIEWER = "viewer"

ROLES = (OWNER, CHIEF, ACCOUNTANT, CASHIER, VIEWER)
ROLE_LABELS = {
    OWNER: "Chủ doanh nghiệp / Giám đốc",
    CHIEF: "Kế toán trưởng",
    ACCOUNTANT: "Kế toán viên",
    CASHIER: "Thủ quỹ",
    VIEWER: "Chỉ xem",
}

ALL_ROLES = frozenset(ROLES)
MANAGERS = frozenset({OWNER, CHIEF})
ACCOUNTANTS = frozenset({OWNER, CHIEF, ACCOUNTANT})
BOOKKEEPERS = frozenset({OWNER, CHIEF, ACCOUNTANT, CASHIER})
READERS = frozenset({OWNER, CHIEF, ACCOUNTANT, VIEWER})

# Permission naming: <module>.view | .export | .create (draft) | .post |
# .cancel | .manage | ...
# API tokens with scope "read" may only use .view/.export permissions;
# "write_draft" adds .create.
READ_SUFFIXES = (".view", ".export")
DRAFT_SUFFIXES = (".create",)

# ---------------------------------------------------------- navigation

SECTIONS: list[tuple[str, str, str]] = [
    ("overview", "Tổng quan", "layout-dashboard"),
    ("money", "Tiền", "wallet"),
    ("sales", "Bán hàng", "shopping-cart"),
    ("purchases", "Mua hàng", "truck"),
    ("inventory", "Kho", "boxes"),
    ("assets", "Tài sản", "building"),
    ("payroll", "Lương", "users"),
    ("tax", "Thuế", "percent"),
    ("books", "Sổ sách & báo cáo", "book-open"),
    ("catalog", "Danh mục", "clipboard-list"),
    ("system", "Hệ thống", "settings"),
]


@dataclass(frozen=True)
class NavItem:
    section: str
    label: str
    href: str
    icon: str = "file-text"
    perm: str | None = None
    order: int = 100
    # Path prefix used to mark the item as active (default = href).
    match: str | None = None


@dataclass(frozen=True)
class QuickAction:
    label: str
    href: str
    icon: str = "plus"
    perm: str | None = None
    shortcut: str | None = None
    group: str = "Tạo mới"
    order: int = 100


@dataclass(frozen=True)
class DocType:
    """Document type (voucher number prefix). url uses `{id}`."""

    code: str
    label: str
    url: str = "/entries/{id}"
    # Permission prefix of the owning module: "<perm>.view",
    # "<perm>.create", "<perm>.post", "<perm>.cancel"
    perm: str = "journal"


@dataclass
class AiTool:
    name: str
    description: str
    parameters: dict
    func: Callable[..., Any]
    perm: str | None = None
    write: bool = False
    label: str | None = None

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class DataCheck:
    code: str
    title: str
    func: Callable[..., Any]
    perm: str | None = None
    order: int = 100


@dataclass
class CheckIssue:
    """A single finding of a data check."""

    message: str
    severity: str = "warning"  # error | warning | info
    link: str | None = None
    date: Any = None


@dataclass
class Registry:
    nav: list[NavItem] = field(default_factory=list)
    actions: list[QuickAction] = field(default_factory=list)
    doc_types: dict[str, DocType] = field(default_factory=dict)
    permissions: dict[str, frozenset[str]] = field(default_factory=dict)
    ai_tools: dict[str, AiTool] = field(default_factory=dict)
    checks: list[DataCheck] = field(default_factory=list)
    posting_validators: list[Callable[..., Any]] = field(default_factory=list)
    hooks: dict[str, list[Callable[..., Any]]] = field(default_factory=dict)
    loaded: bool = False


registry = Registry()

# Shared document types (a module may override the url with its own
# DOC_TYPES).
CORE_DOC_TYPES = [
    DocType("PT", "Phiếu thu", perm="cash"),
    DocType("PC", "Phiếu chi", perm="cash"),
    DocType("BC", "Thu tiền gửi", perm="bank"),
    DocType("BN", "Chi tiền gửi", perm="bank"),
    DocType("CK", "Chuyển tiền nội bộ", perm="bank"),
    DocType("PK", "Phiếu kế toán"),
    DocType("BH", "Bán hàng", perm="sales"),
    DocType("MH", "Mua hàng", perm="purchases"),
    DocType("TL", "Giảm giá, trả lại hàng bán", perm="sales"),
    DocType("TM", "Trả lại hàng mua", perm="purchases"),
    DocType("NK", "Nhập kho", perm="inventory"),
    DocType("XK", "Xuất kho", perm="inventory"),
    DocType("GV", "Giá vốn hàng xuất", perm="inventory"),
    DocType("KH", "Khấu hao TSCĐ", perm="assets"),
    DocType("TS", "Ghi giảm TSCĐ", perm="assets"),
    DocType("PB", "Phân bổ chi phí", perm="assets"),
    DocType("LG", "Hạch toán lương", perm="payroll"),
    DocType("KC", "Kết chuyển"),
]
for _d in CORE_DOC_TYPES:
    registry.doc_types[_d.code] = _d


# ---------------------------------------------------------- decorators


def ai_tool(
    *,
    name: str,
    description: str,
    parameters: dict | None = None,
    perm: str | None = None,
    write: bool = False,
    label: str | None = None,
):
    """Register an AI tool in OpenAI function style.

    The function takes (db, ctx, **arguments) and returns a
    JSON-serializable dict.
    """

    def deco(func):
        registry.ai_tools[name] = AiTool(
            name=name,
            description=description,
            parameters=parameters or {"type": "object", "properties": {}},
            func=func,
            perm=perm,
            write=write,
            label=label,
        )
        return func

    return deco


def data_check(
    *, code: str, title: str, perm: str | None = None, order: int = 100
):
    """Register a data check.

    The function takes (db, ctx) and returns list[CheckIssue].
    """

    def deco(func):
        registry.checks = [c for c in registry.checks if c.code != code]
        registry.checks.append(
            DataCheck(
                code=code, title=title, func=func, perm=perm, order=order
            )
        )
        return func

    return deco


def posting_validator(func):
    """Register a validator run on posting/cancelling.

    Signature: func(db, ctx, entry, action) where action is 'post' |
    'cancel'. Runs inside the posting transaction, after the status has
    changed and been flushed. Raise BusinessError to block the action.
    """
    if func not in registry.posting_validators:
        registry.posting_validators.append(func)
    return func


def on(event: str):
    """Register a hook for an event.

    Events: 'entry.posted', 'entry.cancelled', 'entry.deleted'.
    Signature: func(db, ctx, entry).
    """

    def deco(func):
        lst = registry.hooks.setdefault(event, [])
        if func not in lst:
            lst.append(func)
        return func

    return deco


def emit(event: str, *args, **kwargs) -> None:
    for func in registry.hooks.get(event, []):
        func(*args, **kwargs)


# ------------------------------------------------------ module loading

OPTIONAL_MODULES = ("models", "perms", "nav", "ai_tools", "checks", "hooks")


def tolerant() -> bool:
    """Parallel development mode.

    A feature module that fails to import is skipped (with a warning)
    instead of breaking the whole app.
    """
    import os

    return os.environ.get("VFAS_TOLERANT_FEATURES") == "1"


def _import_optional(name: str):
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as e:
        if e.name == name:
            return None
        if tolerant():
            import warnings

            warnings.warn(f"Skipping {name}: {e!r}", stacklevel=2)
            return None
        raise
    except Exception as e:
        if tolerant():
            import warnings

            warnings.warn(f"Skipping {name}: {e!r}", stacklevel=2)
            return None
        raise


def feature_names() -> list[str]:
    import app.features as pkg

    return sorted(
        m.name for m in pkgutil.iter_modules(pkg.__path__) if m.ispkg
    )


def load_features() -> Registry:
    """Load models/perms/nav/ai_tools/checks/hooks of every feature.

    Runs only once.
    """
    if registry.loaded:
        return registry
    from app.core import perms as core_perms

    registry.permissions.update(core_perms.PERMISSIONS)
    for feat in feature_names():
        for sub in OPTIONAL_MODULES:
            mod = _import_optional(f"app.features.{feat}.{sub}")
            if mod is None:
                continue
            if sub == "perms":
                for perm, roles in getattr(mod, "PERMISSIONS", {}).items():
                    registry.permissions[perm] = frozenset(roles)
            elif sub == "nav":
                registry.nav.extend(getattr(mod, "NAV", []))
                registry.actions.extend(getattr(mod, "ACTIONS", []))
                for d in getattr(mod, "DOC_TYPES", []):
                    registry.doc_types[d.code] = d
    registry.nav.sort(key=lambda n: (n.order, n.label))
    registry.actions.sort(key=lambda a: (a.order, a.label))
    registry.checks.sort(key=lambda c: (c.order, c.code))
    registry.loaded = True
    return registry


def roles_for(perm: str) -> frozenset[str]:
    load_features()
    return registry.permissions.get(perm, frozenset())


def doc_url(doc_type: str, entry_id: int) -> str:
    d = registry.doc_types.get(doc_type)
    return (d.url if d else "/entries/{id}").format(id=entry_id)


def can_entry(user, doc_type: str, action: str) -> bool:
    """Check an action on an entry against its owning module.

    Permissions come from the module that owns the document type; for
    viewing, journal.view is also sufficient.
    """
    if user is None:
        return True
    d = registry.doc_types.get(doc_type)
    prefix = d.perm if d else "journal"
    if user.can(f"{prefix}.{action}"):
        return True
    return (
        action == "view" and prefix != "payroll" and user.can("journal.view")
    )


def doc_label(doc_type: str) -> str:
    d = registry.doc_types.get(doc_type)
    return d.label if d else doc_type
