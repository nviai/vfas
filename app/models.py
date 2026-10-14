"""Import every model so that Base.metadata is complete.

Needed by Alembic, backup/restore and the tests. Each feature keeps its
models in app/features/<feature>/models.py (and *_models.py); they are
imported automatically.
"""

from __future__ import annotations

import importlib
import pkgutil

import app.features as _features
from app.core.audit import AuditLog  # noqa: F401
from app.core.db import Base  # noqa: F401


def _load_models() -> None:
    for mod in sorted(
        pkgutil.iter_modules(_features.__path__), key=lambda m: m.name
    ):
        if not mod.ispkg:
            continue
        pkg_path = f"app.features.{mod.name}"
        try:
            pkg = importlib.import_module(pkg_path)
        except ModuleNotFoundError:
            continue
        for sub in sorted(
            pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name
        ):
            if sub.name == "models" or sub.name.endswith("_models"):
                try:
                    importlib.import_module(f"{pkg_path}.{sub.name}")
                except Exception as e:
                    from app.core.registry import tolerant

                    if not tolerant():
                        raise
                    import warnings

                    warnings.warn(
                        f"Skipping model {pkg_path}.{sub.name}: {e!r}",
                        stacklevel=2,
                    )


_load_models()
