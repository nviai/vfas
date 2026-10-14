"""Annual financial statements (TT133) as HTKK XML.

HTKK form 683 "Bộ báo cáo tài chính (B01a - DNN)", XML version 2.3.2,
checked against the official schema 133_B01A_DNN_BCTC_232.xsd:

- CTieuTKhaiChinh = B01a-DNN, three parallel blocks ThuyetMinh /
  SoCuoiNam / SoDauNam with the same 47 line codes;
- PLuc/PL_KQHDSXKD = B02-DNN, PLuc/PL_LCTTTT = B03-DNN (direct
  method), blocks ThuyetMinh / NamNay / NamTruoc;
- PLuc/PL_CDTK = F01-DNN on the fixed list of 123 TT133 accounts plus
  tongCong; detail accounts opened by the company are rolled up into
  their listed parent.

Lines marked (*) and cash outflows are written as negative numbers, as
on the printed forms. The notes (B09-DNN) are not part of the XML: they
are attached as a file when filing. The period encoding of this form
(kieuKy) is not documented; HTKK's own template pre-fills "D", which is
used here with the fiscal-year end date – check the file in HTKK
("Nhập tờ khai từ XML") before filing.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.core import htkk
from app.core.htkk import el, num
from app.features.gl import books, periods, statements
from app.features.org.models import Company

FORM = htkk.FormInfo(
    code="683",
    name="Bộ báo cáo tài chính (B01a - DNN)(TT133/2016/TT-BTC)",
    note=(
        "(Ban hành theo Thông tư số 133/2016/TT-BTC ngày 26/8/2016 "
        "của Bộ Tài chính)"
    ),
    version="2.3.2",
)

B01A_CODES = (
    "110", "120", "121", "122", "123", "124", "130", "131", "132", "133",
    "134", "135", "136", "140", "141", "142", "150", "151", "152", "160",
    "161", "162", "170", "180", "181", "182", "200", "300", "311", "312",
    "313", "314", "315", "316", "317", "318", "319", "320", "400", "411",
    "412", "413", "414", "415", "416", "417", "500",
)  # fmt: skip
B02_CODES = (
    "01", "02", "10", "11", "20", "21", "22", "23", "24", "30", "31",
    "32", "40", "50", "51", "60",
)  # fmt: skip
B03_CODES = (
    "01", "02", "03", "04", "05", "06", "07", "20", "21", "22", "23",
    "24", "25", "30", "31", "32", "33", "34", "35", "40", "50", "60",
    "61", "70",
)  # fmt: skip
# Accounts of the F01-DNN block, in schema order (the TT133 chart).
F01_ACCOUNTS = (
    "111", "1111", "1112", "112", "1121", "1122", "121", "128", "1281",
    "1288", "131", "133", "1331", "1332", "136", "1361", "1368", "138",
    "1381", "1386", "1388", "141", "151", "152", "153", "154", "155",
    "156", "157", "211", "2111", "2112", "2113", "214", "2141", "2142",
    "2143", "2147", "217", "228", "2281", "2288", "229", "2291", "2292",
    "2293", "2294", "241", "2411", "2412", "2413", "242", "331", "333",
    "3331", "33311", "33312", "3332", "3333", "3334", "3335", "3336",
    "3337", "3338", "33381", "33382", "3339", "334", "335", "336",
    "3361", "3368", "338", "3381", "3382", "3383", "3384", "3385",
    "3386", "3387", "3388", "341", "3411", "3412", "352", "3521", "3522",
    "3524", "353", "3531", "3532", "3533", "3534", "356", "3561", "3562",
    "411", "4111", "4112", "4118", "413", "418", "419", "421", "4211",
    "4212", "511", "5111", "5112", "5113", "5118", "515", "611", "631",
    "632", "635", "642", "6421", "6422", "711", "811", "821", "911",
)  # fmt: skip


def _blocks(st, codes, current_tag: str, previous_tag: str) -> str:
    rows = {r.line.code: r for r in st.rows}
    notes = "".join(
        el(f"ct{c}", rows[c].line.note if c in rows else "") for c in codes
    )
    cur = "".join(
        num(f"ct{c}", rows[c].current if c in rows else 0) for c in codes
    )
    prev = "".join(
        num(f"ct{c}", rows[c].previous if c in rows else 0) for c in codes
    )
    return (
        f"<ThuyetMinh>{notes}</ThuyetMinh>"
        f"<{current_tag}>{cur}</{current_tag}>"
        f"<{previous_tag}>{prev}</{previous_tag}>"
    )


def _f01(db: Session, company: Company, start: date, end: date) -> str:
    tb = books.trial_balance(db, company.id, start, end, level=9)
    rows = {r.code: r for r in tb.rows}

    def side(field: str) -> str:
        values = "".join(
            num(f"ct{a}", getattr(rows[a], field) if a in rows else 0)
            for a in F01_ACCOUNTS
        )
        return values + num("tongCong", getattr(tb.total, field))

    blocks = (
        ("SoDuDauKy", "open_d", "open_c"),
        ("SoPhatSinhTrongKy", "per_d", "per_c"),
        ("SoDuCuoiKy", "close_d", "close_c"),
    )
    return (
        "<PL_CDTK>"
        + "".join(
            f"<{tag}><No>{side(d)}</No><Co>{side(c)}</Co></{tag}>"
            for tag, d, c in blocks
        )
        + "</PL_CDTK>"
    )


def unmapped_accounts(db: Session, company: Company, year: int) -> list[str]:
    """Accounts with figures that have no element in the F01 block.

    Accounts opened by the company under a listed account are rolled up
    into it; anything else would be lost in the XML.
    """
    tb = books.trial_balance(
        db, company.id, date(year, 1, 1), date(year, 12, 31), level=9
    )
    listed = set(F01_ACCOUNTS)
    out = []
    for r in tb.rows:
        if r.is_zero or r.code in listed:
            continue
        if not any(
            r.code.startswith(a) for a in listed if len(a) < len(r.code)
        ):
            out.append(r.code)
    return out


def build_xml(
    db: Session,
    company: Company,
    year: int,
    *,
    audited: bool = False,
    signed_on: date | None = None,
) -> bytes:
    signed_on = signed_on or date.today()
    period = periods.make("year", year)
    b01 = statements.b01a(db, company, period)
    b02 = statements.b02(db, company, period)
    b03 = statements.b03(db, company, period)
    main = (
        el("bctcDaKiemToan", audited)
        + el("maYKienKToan")
        + el("tenYKienKToan")
        + _blocks(b01, B01A_CODES, "SoCuoiNam", "SoDauNam")
        + el("nguoiLapBieu", company.preparer)
        + el("keToanTruong", company.chief_accountant)
        + el("ngayLap", signed_on.isoformat())
        + el("nguoiDaiDienTheoPhapLuat", company.legal_rep)
    )
    appendices = (
        "<PL_KQHDSXKD>"
        + _blocks(b02, B02_CODES, "NamNay", "NamTruoc")
        + "</PL_KQHDSXKD><PL_LCTTTT>"
        + _blocks(b03, B03_CODES, "NamNay", "NamTruoc")
        + "</PL_LCTTTT>"
        + _f01(db, company, period.start, period.end)
    )
    return htkk.document(
        company,
        FORM,
        htkk.Period("D", f"{period.end:%d/%m/%Y}", period.start, period.end),
        main,
        appendices,
        signer=company.legal_rep,
        signed_on=signed_on,
    )
