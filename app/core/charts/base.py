"""Turn a chart table into ChartAccount rows.

A chart table row is (code, name, normal balance, flags):
  balance: D = debit, C = credit, B = both, N = no balance (TK 911)
  flags:   P = partner required, p = partner optional,
           K = bank account required,
           I = tracks inventory items (optional),
           A = tracks fixed assets (optional),
           X = must not go negative (checked retroactively)
Parent accounts (those with sub-accounts) cannot be posted to directly.
"""

from __future__ import annotations

from dataclasses import dataclass

KIND_BY_CLASS = {
    "1": "asset",
    "2": "asset",
    "3": "liability",
    "4": "equity",
    "5": "revenue",
    "6": "expense",
    "7": "revenue",
    "8": "expense",
    "9": "result",
}
NORMAL = {"D": "debit", "C": "credit", "B": "both", "N": "none"}


@dataclass(frozen=True)
class ChartAccount:
    code: str
    name: str
    parent_code: str | None
    level: int
    kind: str
    normal: str
    is_postable: bool
    partner_mode: str  # none | optional | required
    needs_bank: bool
    tracks_item: bool
    tracks_asset: bool
    no_negative: bool


def _parent(code: str, codes: set[str]) -> str | None:
    for n in range(len(code) - 1, 2, -1):
        if code[:n] in codes:
            return code[:n]
    return None


def build(chart: tuple[tuple[str, str, str, str], ...]) -> list[ChartAccount]:
    codes = {c[0] for c in chart}
    parents = {_parent(c, codes) for c in codes} - {None}
    out = []
    for code, name, normal, flags in chart:
        parent = _parent(code, codes)
        level = 1
        p = parent
        while p:
            level += 1
            p = _parent(p, codes)
        out.append(
            ChartAccount(
                code=code,
                name=name,
                parent_code=parent,
                level=level,
                kind=KIND_BY_CLASS[code[0]],
                normal=NORMAL[normal],
                is_postable=code not in parents,
                partner_mode="required"
                if "P" in flags
                else "optional"
                if "p" in flags
                else "none",
                needs_bank="K" in flags,
                tracks_item="I" in flags,
                tracks_asset="A" in flags,
                no_negative="X" in flags,
            )
        )
    return out
