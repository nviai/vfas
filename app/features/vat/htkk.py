"""Build the 01/GTGT VAT return XML in the HTKK export format.

The file is meant for filing through the e-tax portal or for HTKK's
"Nhập tờ khai từ XML" import. Structure: HTKK form 842, version 2.8.3,
checked against the official validation schema 01_GTGT_TT80_283.xsd
(see app/core/htkk.py and docs/DECISIONS.md D-v2-12). Open the file in
HTKK before filing: Circular 89/2026 will bring a new version.
"""

from __future__ import annotations

from datetime import date

from app.core import htkk
from app.core.htkk import el
from app.features.org.models import Company
from app.features.vat.service import TaxPeriod

NAMESPACE = htkk.NAMESPACE
FORM = htkk.FormInfo(
    code="842",
    name="TỜ KHAI THUẾ GIÁ TRỊ GIA TĂNG (Mẫu số 01/GTGT)",
    note=(
        "(Ban hành kèm theo Thông tư số 80/2021/TT-BTC ngày 29 tháng 9 "
        "năm 2021 của Bộ trưởng Bộ Tài chính)"
    ),
    version="2.8.3",
)
# Ordinary business activity, VAT sub-item 1701 (HTKK catalogue
# catalogue_dm_nnkd.xml, key 01_GTGT_TT80).
BUSINESS_LINE = ("00", "Hoạt động sản xuất kinh doanh thông thường")
SUB_ITEM = "1701"

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
    body = [
        el("ma_NganhNghe", BUSINESS_LINE[0]),
        el("ten_NganhNghe", BUSINESS_LINE[1]),
        el("tieuMucHachToan", SUB_ITEM),
        # Dependent unit / business location in another province: none.
        "<Header>"
        + el("ct09")
        + el("ct10")
        + "<DiaChiHDSXKDKhacTinhNDTSC>"
        + "".join(
            el(t)
            for t in (
                "ct11a_phuongXa_ma",
                "ct11a_phuongXa_ten",
                "ct11b_quanHuyen_ma",
                "ct11b_quanHuyen_ten",
                "ct11c_tinhTP_ma",
                "ct11c_tinhTP_ten",
            )
        )
        + "</DiaChiHDSXKDKhacTinhNDTSC></Header>",
    ]
    for item in LAYOUT:
        if isinstance(item, tuple):
            group, keys = item
            body.append(
                f"<{group}>"
                + "".join(el(f"ct{k}", _num(values, k)) for k in keys)
                + f"</{group}>"
            )
        else:
            body.append(el(f"ct{item}", _num(values, item)))
    kind = "M" if period.period_type == "month" else "Q"
    return htkk.document(
        company,
        FORM,
        htkk.Period(kind, period.code, period.start, period.end),
        "".join(body),
        signer=signer,
        signed_on=signed_on,
        version=version,
    )
