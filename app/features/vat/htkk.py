"""Build 01/GTGT VAT return XML in the HTKK export format.

The file is meant for filing through the e-tax portal.

WARNING: no Ministry of Finance XSD was at hand when this module was
written; the structure below follows common HTKK files
(HSoThueDTu/HSoKhaiThue/TTinChung + CTieuTKhaiChinh with ctNN
elements). Before filing, open the file in HTKK
("Nhập tờ khai từ XML") or use the e-tax portal's validation to
compare. See docs/requests/vat.md.
"""

from __future__ import annotations

from datetime import date
from xml.sax.saxutils import escape

from app.features.org.models import Company
from app.features.vat.service import TaxPeriod

NAMESPACE = "http://kekhaithue.gdt.gov.vn/TKhaiThue"
# HTKK form code of 01/GTGT (Circular 80) – check it against the HTKK
# version in use.
MA_TKHAI = "842"
PBAN_XML = "2.7.1"
FORM_NOTE = (
    "(Ban hành kèm theo Thông tư số 80/2021/TT-BTC ngày 29 tháng 9 "
    "năm 2021 của Bộ trưởng Bộ Tài chính, "
    "được sửa đổi, bổ sung bởi Thông tư số 40/2025/TT-BTC)"
)

# Element order inside CTieuTKhaiChinh; nested groups follow the form
# layout.
LAYOUT: list = [
    "21",
    "22",
    ("GiaTriVaThueGTGTHHDVMuaVao", ["23", "24"]),
    ("HangHoaDichVuNhapKhau", ["23a", "24a"]),
    "25",
    "26",
    ("HHDVBRaChiuThueGTGT", ["27", "28"]),
    "29",
    ("HHDVBRaChiuTSuat5", ["30", "31"]),
    ("HHDVBRaChiuTSuat10", ["32", "33"]),
    "32a",
    ("TongDThuVaThueGTGTHHDVBRa", ["34", "35"]),
    "36",
    "37",
    "38",
    "39a",
    "40a",
    "40b",
    "40",
    "41",
    "42",
    "43",
]


def _el(tag: str, value) -> str:
    if value is None or value == "":
        return f"<{tag}/>"
    return f"<{tag}>{escape(str(value))}</{tag}>"


def _num(values: dict, key: str) -> str:
    if key == "21":
        return "1" if values.get("21") else "0"
    return str(int(values.get(key) or 0))


def build_xml(
    company: Company,
    period: TaxPeriod,
    values: dict,
    *,
    signer: str = "",
    signed_on: date | None = None,
    version: int = 0,
) -> bytes:
    signed_on = signed_on or date.today()
    kieu_ky = "M" if period.period_type == "month" else "Q"
    body = []
    for item in LAYOUT:
        if isinstance(item, tuple):
            group, keys = item
            body.append(
                f"<{group}>"
                + "".join(_el(f"ct{k}", _num(values, k)) for k in keys)
                + f"</{group}>"
            )
        else:
            body.append(_el(f"ct{item}", _num(values, item)))
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<HSoThueDTu xmlns="{NAMESPACE}" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        "<HSoKhaiThue><TTinChung>"
        "<TTinDVu>"
        + _el("maDVu", "VFAS")
        + _el("tenDVu", "VFAS")
        + _el("pbanDVu", "2.0")
        + _el("ttinNhaCCapDVu", "")
        + "</TTinDVu>"
        "<TTinTKhaiThue><TKhaiThue>"
        + _el("maTKhai", MA_TKHAI)
        + _el("tenTKhai", "TỜ KHAI THUẾ GIÁ TRỊ GIA TĂNG (Mẫu số 01/GTGT)")
        + _el("moTaBMau", FORM_NOTE)
        + _el("pbanTKhaiXML", PBAN_XML)
        + _el("loaiTKhai", "C" if version == 0 else "B")
        + _el("soLan", version)
        + "<KyKKhaiThue>"
        + _el("kieuKy", kieu_ky)
        + _el("kyKKhai", period.code)
        + _el("kyKKhaiTuNgay", period.start.strftime("%d/%m/%Y"))
        + _el("kyKKhaiDenNgay", period.end.strftime("%d/%m/%Y"))
        + _el("kyKKhaiTuThang", "")
        + _el("kyKKhaiDenThang", "")
        + "</KyKKhaiThue>"
        + _el("maCQTNoiNop", "")
        + _el("tenCQTNoiNop", company.tax_office or "")
        + _el("ngayLapTKhai", signed_on.isoformat())
        + _el("nguoiKy", signer or company.legal_rep or "")
        + _el("ngayKy", signed_on.isoformat())
        + _el("nganhNgheKD", "")
        + "</TKhaiThue>"
        "<NNT>"
        + _el("mst", company.tax_code)
        + _el("tenNNT", company.name)
        + _el("dchiNNT", company.address)
        + _el("phuongXa", "")
        + _el("maHuyenNNT", "")
        + _el("tenHuyenNNT", "")
        + _el("maTinhNNT", "")
        + _el("tenTinhNNT", company.province or "")
        + _el("dthoaiNNT", company.phone)
        + _el("faxNNT", "")
        + _el("emailNNT", company.email)
        + "</NNT>"
        "</TTinTKhaiThue></TTinChung>"
        "<CTieuTKhaiChinh>" + "".join(body) + "</CTieuTKhaiChinh>"
        "</HSoKhaiThue><CKyDTu/></HSoThueDTu>"
    )
    return xml.encode("utf-8")
