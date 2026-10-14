from app.core.registry import ACCOUNTANTS

PERMISSIONS = {
    # Period-end closing transfers, recording corporate income tax
    # expense
    "closing.run": ACCOUNTANTS,
}
