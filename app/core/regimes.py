"""Accounting regimes: chart of accounts + the "Ban hành theo …" line.

The "issued under" line is printed on report and voucher forms.

Adding a regime (TT99/2025 for large enterprises, TT58/2026 for
micro enterprises) means adding a chart module in app/core/charts/ and
an entry in REGIMES; a company picks one via the `companies.regime`
column. Forms specific to a regime live in the matching feature module.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.core.charts import tt133


@dataclass(frozen=True)
class Regime:
    code: str
    name: str
    issued: str
    chart: Callable[[], list]
    available: bool = True


REGIMES: dict[str, Regime] = {
    "tt133": Regime(
        "tt133",
        "Thông tư 133/2016/TT-BTC (doanh nghiệp nhỏ và vừa)",
        tt133.ISSUED,
        tt133.chart_accounts,
    ),
}


def get_regime(code: str | None) -> Regime:
    return REGIMES.get((code or "tt133").lower(), REGIMES["tt133"])
