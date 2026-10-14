"""Simulated provider for trying the issuing flow and for tests.

Nothing leaves the server and the invoices have NO legal value: the
tax authority code is a random string marked "MO PHONG". Only offered
when EINVOICE_SIMULATOR=true (never enable it on a real installation).
State lives in process memory, keyed by seller tax code.
"""

from __future__ import annotations

import secrets
import threading
from datetime import datetime
from xml.sax.saxutils import escape

from app.features.einvoice.providers.base import (
    ISSUED,
    CanonicalInvoice,
    EInvoiceProvider,
    IssueResult,
    ProviderRejectedError,
    vat_rate_label,
)

_LOCK = threading.Lock()
# (tax code, key) -> IssueResult
_ISSUED: dict[tuple[str, str], IssueResult] = {}
# (tax code, template, series) -> last number
_NUMBERS: dict[tuple[str, str, str], int] = {}


def reset() -> None:
    with _LOCK:
        _ISSUED.clear()
        _NUMBERS.clear()


class SimulatorProvider(EInvoiceProvider):
    code = "simulator"
    name = "Mô phỏng (không có giá trị pháp lý)"
    fields = ()
    async_tax_code = False

    def test_connection(self) -> str:
        return "Nhà cung cấp mô phỏng sẵn sàng"

    def issue(self, invoice: CanonicalInvoice) -> IssueResult:
        if not invoice.lines:
            raise ProviderRejectedError("Hóa đơn không có dòng hàng hóa")
        if invoice.is_adjustment and invoice.original is None:
            raise ProviderRejectedError("Thiếu hóa đơn gốc bị điều chỉnh")
        key = (invoice.seller_tax_code, invoice.key)
        with _LOCK:
            if key in _ISSUED:  # repeated request: same invoice
                return _ISSUED[key]
            nkey = (invoice.seller_tax_code, invoice.template, invoice.series)
            _NUMBERS[nkey] = _NUMBERS.get(nkey, 0) + 1
            result = IssueResult(
                status=ISSUED,
                number=str(_NUMBERS[nkey]),
                template=invoice.template,
                series=invoice.series,
                issue_date=invoice.issue_date,
                provider_ref=f"SIM-{invoice.key[:12]}",
                lookup_code=secrets.token_hex(5).upper(),
                tax_authority_code="MO PHONG-" + secrets.token_hex(8).upper(),
                raw={"simulated": True},
            )
            _ISSUED[key] = result
            return result

    def find(self, invoice: CanonicalInvoice) -> IssueResult | None:
        with _LOCK:
            return _ISSUED.get((invoice.seller_tax_code, invoice.key))

    def download(
        self, invoice: CanonicalInvoice, result: IssueResult, kind: str
    ) -> bytes:
        if kind == "xml":
            return build_hdon_xml(invoice, result)
        from app.core.pdf import render_pdf

        return render_pdf(
            "einvoice/print/simulated_invoice.html",
            {"inv": invoice, "res": result, "rate_label": vat_rate_label},
        )


def _el(tag: str, value) -> str:
    return f"<{tag}>{escape(str(value))}</{tag}>"


def build_hdon_xml(invoice: CanonicalInvoice, result: IssueResult) -> bytes:
    """Invoice data in the TT78 `HDon` layout (unsigned, simulated)."""
    rows = []
    for ln in invoice.lines:
        rows.append(
            "<HHDVu>"
            + _el("TChat", 1)
            + _el("STT", ln.line_no)
            + _el("MHHDVu", ln.code)
            + _el("THHDVu", ln.name)
            + _el("DVTinh", ln.unit)
            + _el("SLuong", ln.quantity if ln.quantity is not None else "")
            + _el("DGia", ln.unit_price if ln.unit_price is not None else "")
            + _el("STCKhau", ln.discount)
            + _el("ThTien", ln.amount)
            + _el("TSuat", vat_rate_label(ln.vat_rate))
            + "</HHDVu>"
        )
    groups = "".join(
        "<LTSuat>"
        + _el("TSuat", vat_rate_label(g.vat_rate))
        + _el("ThTien", g.base)
        + _el("TThue", g.vat)
        + "</LTSuat>"
        for g in invoice.tax_groups
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?><HDon><DLHDon>'
        "<TTChung>"
        + _el("PBan", "2.1.0")
        + _el("THDon", "HÓA ĐƠN GIÁ TRỊ GIA TĂNG (MÔ PHỎNG)")
        + _el("KHMSHDon", result.template)
        + _el("KHHDon", result.series)
        + _el("SHDon", result.number)
        + _el("NLap", (result.issue_date or invoice.issue_date).isoformat())
        + _el("DVTTe", invoice.currency)
        + _el("TGia", 1)
        + _el("HTTToan", invoice.payment_method)
        + "</TTChung><NDHDon><NBan>"
        + _el("MST", invoice.seller_tax_code)
        + "</NBan><NMua>"
        + _el("Ten", invoice.buyer_legal_name)
        + _el("MST", invoice.buyer_tax_code)
        + _el("DChi", invoice.buyer_address)
        + _el("HVTNMHang", invoice.buyer_name)
        + "</NMua><DSHHDVu>"
        + "".join(rows)
        + "</DSHHDVu><TToan><THTTLTSuat>"
        + groups
        + "</THTTLTSuat>"
        + _el("TgTCThue", invoice.total_before_tax)
        + _el("TgTThue", invoice.total_vat)
        + _el("TTCKTMai", 0)
        + _el("TgTTTBSo", invoice.total)
        + _el("TgTTTBChu", invoice.amount_in_words)
        + "</TToan></NDHDon></DLHDon>"
        + _el("MCCQT", result.tax_authority_code)
        + _el("TGianTao", datetime.now().isoformat(timespec="seconds"))
        + "</HDon>"
    )
    return xml.encode("utf-8")
