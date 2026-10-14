"""Envelope of tax-return XML files in the HTKK layout.

Structure checked against the validation schemas shipped inside the
HTKK application (InterfaceTemplates/TKhaiThue.xsd,
TKhaiThue_DBHC.xsd and Validate/<form>_<version>.xsd, HTKK 5.4.5 and
5.6.4) – see docs/DECISIONS.md D-v2-12. The files are not signed:
HTKK and the e-tax portal sign them when the return is filed, so no
<CKyDTu> element is written (an empty one fails validation).

Each form module builds the content of CTieuTKhaiChinh / PLuc and
calls `document()`. Values are integers (an empty xs:long fails
validation); missing optional strings are written as empty elements.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from xml.sax.saxutils import escape

NAMESPACE = "http://kekhaithue.gdt.gov.vn/TKhaiThue"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
SERVICE = ("VFAS", "VFAS", "2.1")


@dataclass(frozen=True)
class FormInfo:
    code: str  # maTKhai
    name: str  # tenTKhai
    note: str  # moTaBMau
    version: str  # pbanTKhaiXML
    # The "địa bàn hành chính" envelope (tenXaNNT/maXaNNT instead of
    # phuongXa) used by newer form versions.
    dbhc: bool = False


@dataclass(frozen=True)
class Period:
    kind: str  # kieuKy: M | Q | Y | D
    code: str  # kyKKhai
    from_date: date | None = None
    to_date: date | None = None
    from_month: str = ""  # MM/YYYY
    to_month: str = ""


def el(tag: str, value=None) -> str:
    if value is None or value == "":
        return f"<{tag}/>"
    if isinstance(value, bool):
        value = "1" if value else "0"
    return f"<{tag}>{escape(str(value))}</{tag}>"


def num(tag: str, value) -> str:
    """Integer element; None/empty is written as 0."""
    return f"<{tag}>{int(value or 0)}</{tag}>"


def nil(tag: str) -> str:
    return f'<{tag} xsi:nil="true"/>'


def tin(tag: str, value: str | None) -> str:
    """Tax code element (10, 10-3 or 12 digits); empty = xsi:nil."""
    v = (value or "").strip()
    return el(tag, v) if v else nil(tag)


def _d(d: date | None) -> str:
    return d.strftime("%d/%m/%Y") if d else ""


def document(
    company,
    form: FormInfo,
    period: Period,
    main: str,
    appendices: str = "",
    *,
    signer: str = "",
    signed_on: date | None = None,
    version: int = 0,
) -> bytes:
    """Wrap the form content into HSoThueDTu and return UTF-8 bytes."""
    signed_on = signed_on or date.today()
    if form.dbhc:
        ward = el("tenXaNNT", "") + el("maXaNNT", "")
    else:
        ward = el("phuongXa", "")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<HSoThueDTu xmlns="{NAMESPACE}" xmlns:xsi="{XSI}">'
        '<HSoKhaiThue id="ID_1"><TTinChung>'
        "<TTinDVu>"
        + el("maDVu", SERVICE[0])
        + el("tenDVu", SERVICE[1])
        + el("pbanDVu", SERVICE[2])
        + el("ttinNhaCCapDVu", "")
        + "</TTinDVu>"
        "<TTinTKhaiThue><TKhaiThue>"
        + el("maTKhai", form.code)
        + el("tenTKhai", form.name)
        + el("moTaBMau", form.note)
        + el("pbanTKhaiXML", form.version)
        + el("loaiTKhai", "C" if version == 0 else "B")
        + el("soLan", version)
        + "<KyKKhaiThue>"
        + el("kieuKy", period.kind)
        + el("kyKKhai", period.code)
        + el("kyKKhaiTuNgay", _d(period.from_date))
        + el("kyKKhaiDenNgay", _d(period.to_date))
        + el("kyKKhaiTuThang", period.from_month)
        + el("kyKKhaiDenThang", period.to_month)
        + "</KyKKhaiThue>"
        + el("maCQTNoiNop", "")
        + el("tenCQTNoiNop", company.tax_office or "")
        + el("ngayLapTKhai", signed_on.isoformat())
        + "<GiaHan>"
        + el("maLyDoGiaHan")
        + el("lyDoGiaHan")
        + "</GiaHan>"
        + el("nguoiKy", signer or company.legal_rep or "")
        + el("ngayKy", signed_on.isoformat())
        + el("nganhNgheKD", "")
        + "</TKhaiThue>"
        "<NNT>"
        + el("mst", company.tax_code)
        + el("tenNNT", company.name)
        + el("dchiNNT", company.address)
        + ward
        + el("maHuyenNNT", "")
        + el("tenHuyenNNT", "")
        + el("maTinhNNT", "")
        + el("tenTinhNNT", company.province or "")
        + el("dthoaiNNT", company.phone)
        + el("faxNNT", "")
        + el("emailNNT", company.email)
        + "</NNT>"
        "</TTinTKhaiThue></TTinChung>"
        f"<CTieuTKhaiChinh>{main}</CTieuTKhaiChinh>"
        + (f"<PLuc>{appendices}</PLuc>" if appendices else "")
        + "</HSoKhaiThue></HSoThueDTu>"
    )
    return xml.encode("utf-8")
