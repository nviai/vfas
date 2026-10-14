"""Keep entry references of the assets and payroll modules consistent
when an entry is cancelled or deleted.
"""

from __future__ import annotations

from sqlalchemy import update

from app.core.registry import on


@on("entry.cancelled")
def _asset_entry_cancelled(db, ctx, entry) -> None:
    if entry.source == "assets" and (entry.meta or {}).get("asset_disposal"):
        from app.features.assets.service import revert_disposal

        revert_disposal(db, entry)


@on("entry.deleted")
def _asset_entry_deleted(db, ctx, entry) -> None:
    from app.features.assets.models import AssetRun, FixedAsset, PrepaidItem
    from app.features.assets.service import revert_disposal

    if (entry.meta or {}).get("asset_disposal"):
        revert_disposal(db, entry)
    cid = entry.company_id
    db.execute(
        update(AssetRun)
        .where(AssetRun.company_id == cid, AssetRun.entry_id == entry.id)
        .values(entry_id=None)
    )
    db.execute(
        update(FixedAsset)
        .where(FixedAsset.company_id == cid, FixedAsset.entry_id == entry.id)
        .values(entry_id=None)
    )
    db.execute(
        update(FixedAsset)
        .where(
            FixedAsset.company_id == cid,
            FixedAsset.disposal_entry_id == entry.id,
        )
        .values(disposal_entry_id=None)
    )
    db.execute(
        update(PrepaidItem)
        .where(PrepaidItem.company_id == cid, PrepaidItem.entry_id == entry.id)
        .values(entry_id=None)
    )
