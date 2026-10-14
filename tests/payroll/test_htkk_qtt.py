"""05/QTT-TNCN XML for HTKK (form 953, version 2.9.3)."""

from datetime import date

from defusedxml import ElementTree

from app.core.registry import ACCOUNTANT
from app.features.org.models import Company
from app.features.payroll import htkk, service
from tests.htkk_schema import validate
from tests.payroll.test_payroll import staff  # noqa: F401 - fixture

NS = {"t": "http://kekhaithue.gdt.gov.vn/TKhaiThue"}


def test_qtt_xml(db, ctxs, staff, company, web, users):  # noqa: F811
    ctx = ctxs[ACCOUNTANT]
    for month in (1, 2):
        service.post_run(db, ctx, service.create_run(db, ctx, 2026, month))
    rows = service.year_summary(db, company.id, 2026)
    c = db.get(Company, company.id)
    data = htkk.build_xml(db, c, 2026, signed_on=date(2027, 3, 1))
    validate(data, "05_QTT_TNCN_TT80_293.xsd")
    root = ElementTree.fromstring(data)
    assert root.find(".//t:maTKhai", NS).text == "953"
    assert root.find(".//t:kyKKhaiTuThang", NS).text == "01/2026"
    # New administrative-area envelope
    assert root.find(".//t:NNT/t:tenXaNNT", NS) is not None
    assert root.find(".//t:NNT/t:phuongXa", NS) is None
    main = root.find(".//t:NVuKhauTruThue", NS)
    assert int(main.find("t:ct16", NS).text) == 3
    assert int(main.find("t:ct17", NS).text) == 2
    assert int(main.find("t:ct31", NS).text) == sum(r.pit for r in rows)
    p1 = root.findall(".//t:PLuc_05_1_BK_QTT/t:BKeCTietCNhan", NS)
    p2 = root.findall(".//t:PLuc_05_2_BK_QTT/t:BKeCTietCNhan", NS)
    assert len(p1) == 2 and len(p2) == 1
    assert p2[0].find("t:ct07", NS).text == "Lê Thời Vụ"
    total = root.find(".//t:PLuc_05_1_BK_QTT/t:ct38", NS)
    assert int(total.text) == sum(int(r.find("t:ct22", NS).text) for r in p1)
    from tests.conftest import ROLE_EMAILS

    web.login(ROLE_EMAILS["chief_accountant"])
    r = web.get("/payroll/reports/qtt.xml?year=2026")
    assert r.status_code == 200 and b"<maTKhai>953</maTKhai>" in r.content
    web.login(ROLE_EMAILS["viewer"])
    assert web.get("/payroll/reports/qtt.xml?year=2026").status_code == 403
