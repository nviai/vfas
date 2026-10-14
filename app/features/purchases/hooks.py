"""Delete the purchase document when its draft entry is deleted."""

from app.core.registry import on


@on("entry.deleted")
def _delete_purchase_doc(db, ctx, entry) -> None:
    if entry.source == "purchases":
        from app.features.purchases.service import delete_for_entry

        delete_for_entry(db, entry.id)
