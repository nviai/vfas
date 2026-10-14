"""Read XML e-invoices (the `HDon` element).

The format follows Decree 123/2020/ND-CP and Circular 78/2021/TT-BTC.
Main structure (namespaces ignored, tags matched case-insensitively):

  HDon/DLHDon/TTChung: KHMSHDon (template no.), KHHDon (series),
      SHDon (number), NLap (issue date), DVTTe, TGia
  HDon/DLHDon/NDHDon/NBan | NMua: Ten, MST, DChi,
      HVTNMHang (buyer's full name)
  HDon/DLHDon/NDHDon/DSHHDVu/HHDVu: TChat, STT, MHHDVu, THHDVu,
      DVTinh, SLuong, DGia, TLCKhau, STCKhau, ThTien, TSuat
  HDon/DLHDon/NDHDon/TToan: THTTLTSuat/LTSuat (TSuat, ThTien, TThue),
      TgTCThue, TgTThue, TTCKTMai, TgTTTBSo
  HDon/MCCQT: tax authority code (for invoices with a code)

The invoice may be wrapped in a `TDiep/DLieu/HDon` message. Parsing
uses defusedxml (blocks XXE and billion laughs attacks).
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from defusedxml import ElementTree as SafeET

from app.core.money import MAX_MONEY
from app.features.sales.common import parse_decimal, round_vnd, vat_of

MAX_FILES = 200
MAX_UNCOMPRESSED = 20 * 1024 * 1024
MAX_FILE = 5 * 1024 * 1024


class EInvoiceError(ValueError):
    pass


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _find(node, *path):
    cur = node
    for name in path:
        if cur is None:
            return None
        nxt = None
        for child in cur:
            if _local(child.tag) == name.lower():
                nxt = child
                break
        cur = nxt
    return cur


def _findall(node, name):
    if node is None:
        return []
    return [c for c in node if _local(c.tag) == name.lower()]


def _text(node, *path) -> str:
    n = _find(node, *path) if path else node
    return (n.text or "").strip() if n is not None and n.text else ""


def _search(root, name):
    for el in root.iter():
        if _local(el.tag) == name.lower():
            return el
    return None


def normalize_rate(value: str) -> str:
    """Normalize a VAT rate to a rate code.

    '10%' → '10'; '8%' → '8'; 'KCT' / 'KKKNT' are kept as is;
    'KHAC:3.5%' → 'KHAC'.
    """
    v = (value or "").strip().upper().replace(" ", "")
    if not v:
        return "KCT"
    if v.startswith("KHAC"):
        return "KHAC"
    if v in ("KCT", "KKKNT"):
        return v
    v = v.rstrip("%").replace(",", ".")
    try:
        d = Decimal(v)
    except Exception:
        return "KHAC"
    if not d.is_finite() or not 0 <= d <= 100:  # NaN, 1e999999…
        return "KHAC"
    if d < 1 and d > 0:  # 0.1 → 10
        d = d * 100
    s = str(d.normalize()) if d != d.to_integral() else str(int(d))
    return s if s in ("0", "5", "8", "10") else "KHAC"


@dataclass
class InvoiceLine:
    description: str
    unit: str
    quantity: Decimal | None
    unit_price: Decimal | None
    amount: int
    vat_rate: str
    vat_amount: int
    code: str = ""
    # TChat: 1 goods/services, 2 promotion, 3 trade discount, 4 note.
    kind: str = "1"


@dataclass
class Party:
    name: str = ""
    tax_code: str = ""
    address: str = ""
    buyer_person: str = ""


@dataclass
class EInvoice:
    template: str  # template number (KHMSHDon)
    series: str  # invoice series (KHHDon)
    number: str
    issue_date: date | None
    currency: str
    seller: Party
    buyer: Party
    lines: list[InvoiceLine] = field(default_factory=list)
    total_before_vat: int = 0
    total_vat: int = 0
    total_payment: int = 0
    rate_totals: dict = field(default_factory=dict)  # rate → (base, vat)
    tax_authority_code: str = ""
    filename: str = ""
    raw: bytes = b""
    warnings: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        """Return the duplicate-guard key.

        Format: seller tax code | template + series | invoice number
        (leading zeros stripped).
        """
        return (
            f"{self.seller.tax_code}|{self.template}{self.series}"
            f"|{(self.number or '').lstrip('0')}"
        )

    @property
    def ref(self) -> str:
        return f"{self.template}{self.series}-{self.number}"


def _int_money(value: str) -> int:
    d = parse_decimal(value, limit=MAX_MONEY)
    return round_vnd(d) if d is not None else 0


def _date(value: str) -> date | None:
    v = (value or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def parse_xml(data: bytes, filename: str = "") -> EInvoice:
    """Parse one XML invoice.

    Any data error (bad number, huge number, NaN...) is turned into a
    user-friendly EInvoiceError instead of a 500 response.
    """
    try:
        return _parse_xml(data, filename)
    except EInvoiceError:
        raise
    except (
        ValueError,
        ArithmeticError,
        TypeError,
        AttributeError,
        KeyError,
        IndexError,
    ) as e:
        raise EInvoiceError(
            f"{filename}: dữ liệu hóa đơn không hợp lệ ({type(e).__name__})"
        ) from e


def _parse_xml(data: bytes, filename: str = "") -> EInvoice:
    if len(data) > MAX_FILE:
        raise EInvoiceError(f"{filename}: file quá lớn")
    try:
        root = SafeET.fromstring(data)
    except Exception as e:  # ParseError, DTDForbidden, EntitiesForbidden…
        raise EInvoiceError(
            f"{filename}: không đọc được XML ({type(e).__name__})"
        ) from e
    hdon = root if _local(root.tag) == "hdon" else _search(root, "HDon")
    if hdon is None:
        raise EInvoiceError(
            f"{filename}: không phải hóa đơn điện tử (thiếu phần tử HDon)"
        )
    dl = _find(hdon, "DLHDon")
    if dl is None:
        raise EInvoiceError(f"{filename}: thiếu phần tử DLHDon")
    tt = _find(dl, "TTChung")
    nd = _find(dl, "NDHDon")
    if tt is None or nd is None:
        raise EInvoiceError(f"{filename}: thiếu TTChung hoặc NDHDon")
    nban, nmua = _find(nd, "NBan"), _find(nd, "NMua")
    seller = Party(_text(nban, "Ten"), _text(nban, "MST"), _text(nban, "DChi"))
    buyer = Party(
        _text(nmua, "Ten") or _text(nmua, "HVTNMHang"),
        _text(nmua, "MST"),
        _text(nmua, "DChi"),
        _text(nmua, "HVTNMHang"),
    )
    inv = EInvoice(
        template=_text(tt, "KHMSHDon"),
        series=_text(tt, "KHHDon"),
        number=_text(tt, "SHDon"),
        issue_date=_date(_text(tt, "NLap")),
        currency=_text(tt, "DVTTe") or "VND",
        seller=seller,
        buyer=buyer,
        tax_authority_code=_text(hdon, "MCCQT"),
        filename=filename,
        raw=data,
    )
    if not inv.number:
        raise EInvoiceError(f"{filename}: thiếu số hóa đơn (SHDon)")
    if inv.currency.upper() not in ("VND", "VNĐ"):
        rate_txt = _text(tt, "TGia")
        inv.warnings.append(
            f"Hóa đơn ngoại tệ {inv.currency} (tỷ giá {rate_txt or '?'}) "
            "– kiểm tra lại số tiền VND"
        )
    for h in _findall(_find(nd, "DSHHDVu"), "HHDVu"):
        kind = _text(h, "TChat") or "1"
        amount = _int_money(_text(h, "ThTien"))
        rate = normalize_rate(_text(h, "TSuat"))
        line = InvoiceLine(
            description=_text(h, "THHDVu"),
            unit=_text(h, "DVTinh"),
            quantity=parse_decimal(_text(h, "SLuong")),
            unit_price=parse_decimal(_text(h, "DGia")),
            amount=amount,
            vat_rate=rate,
            vat_amount=vat_of(amount, rate),
            code=_text(h, "MHHDVu"),
            kind=kind,
        )
        inv.lines.append(line)
    tto = _find(nd, "TToan")
    for lt in _findall(_find(tto, "THTTLTSuat"), "LTSuat"):
        rate = normalize_rate(_text(lt, "TSuat"))
        inv.rate_totals[rate] = (
            _int_money(_text(lt, "ThTien")),
            _int_money(_text(lt, "TThue")),
        )
    inv.total_before_vat = _int_money(_text(tto, "TgTCThue"))
    inv.total_vat = _int_money(_text(tto, "TgTThue"))
    inv.total_payment = _int_money(_text(tto, "TgTTTBSo"))
    _normalize_lines(inv)
    return inv


def _normalize_lines(inv: EInvoice) -> None:
    """Normalize the invoice lines in place.

    Trade discounts (TChat=3) are deducted from a line with the same
    VAT rate, note lines are dropped and line VAT amounts are
    reconciled with the per-rate totals.
    """
    goods = [ln for ln in inv.lines if ln.kind in ("1", "2") and ln.amount > 0]
    for ln in inv.lines:
        if ln.kind == "3" and ln.amount:
            target = next(
                (
                    g
                    for g in reversed(goods)
                    if g.vat_rate == ln.vat_rate and g.amount > abs(ln.amount)
                ),
                None,
            )
            if target is None:
                inv.warnings.append(
                    f"Không trừ được chiết khấu '{ln.description}' "
                    "– kiểm tra tay"
                )
                continue
            target.amount -= abs(ln.amount)
            target.vat_amount = vat_of(target.amount, target.vat_rate)
    inv.lines = goods
    for rate, (base, vat) in inv.rate_totals.items():
        group = [ln for ln in inv.lines if ln.vat_rate == rate]
        if not group:
            continue
        diff_vat = vat - sum(ln.vat_amount for ln in group)
        if diff_vat and abs(diff_vat) <= max(10, len(group) * 2):
            # Rounding difference: add it to the last line, as the
            # invoice does.
            group[-1].vat_amount += diff_vat
        elif diff_vat:
            inv.warnings.append(
                (
                    f"Tiền thuế {rate}% trên dòng lệch tổng hóa đơn "
                    f"{diff_vat:+,} đ"
                ).replace(",", ".")
            )
        diff_base = base - sum(ln.amount for ln in group)
        if diff_base:
            inv.warnings.append(
                (
                    f"Tiền hàng {rate}% trên dòng lệch tổng hóa đơn "
                    f"{diff_base:+,} đ"
                ).replace(",", ".")
            )
    if inv.total_before_vat and inv.total_before_vat != sum(
        ln.amount for ln in inv.lines
    ):
        inv.warnings.append("Tổng tiền hàng các dòng khác tổng trên hóa đơn")


def read_files(
    files: list[tuple[str, bytes]],
) -> tuple[list[EInvoice], list[str]]:
    """Read several .xml or .zip files; return (invoices, errors)."""
    out: list[EInvoice] = []
    errors: list[str] = []
    count = 0
    for name, data in files:
        lower = (name or "").lower()
        if lower.endswith(".zip") or data[:4] == b"PK\x03\x04":
            try:
                zf = zipfile.ZipFile(io.BytesIO(data))
            except Exception:  # noqa: BLE001 – BadZipFile, LargeZipFile, NotImplementedError…
                errors.append(f"{name}: file ZIP hỏng")
                continue
            total = 0
            for info in zf.infolist():
                if info.is_dir() or not info.filename.lower().endswith(".xml"):
                    continue
                count += 1
                total += info.file_size
                if count > MAX_FILES:
                    errors.append(
                        f"Chỉ nhận tối đa {MAX_FILES} hóa đơn mỗi lần"
                    )
                    break
                if total > MAX_UNCOMPRESSED or info.file_size > MAX_FILE:
                    errors.append(f"{name}: dung lượng giải nén vượt giới hạn")
                    break
                short = info.filename.rsplit("/", 1)[-1][:120]
                try:
                    raw = zf.read(info)
                except Exception:  # noqa: BLE001 – bad CRC, encrypted, unsupported compression, zlib.error
                    errors.append(
                        f"{name}/{short}: không giải nén được "
                        "(file hỏng hoặc có mật khẩu)"
                    )
                    continue
                if len(raw) > MAX_FILE:
                    errors.append(f"{name}/{short}: file quá lớn")
                    continue
                try:
                    out.append(parse_xml(raw, short))
                except EInvoiceError as e:
                    errors.append(str(e))
        else:
            count += 1
            if count > MAX_FILES:
                errors.append(f"Chỉ nhận tối đa {MAX_FILES} hóa đơn mỗi lần")
                break
            try:
                out.append(parse_xml(data, name))
            except EInvoiceError as e:
                errors.append(str(e))
    return out, errors
