"""Delete the sales document when its draft journal entry is deleted."""

from app.core.registry import on


@on("entry.deleted")
def _delete_sales_doc(db, ctx, entry) -> None:
    if entry.source == "sales":
        from app.features.sales.service import delete_for_entry

        delete_for_entry(db, entry.id)
