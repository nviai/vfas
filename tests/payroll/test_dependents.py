"""Dependants of employees and appendix 05-3/BK-QTT-TNCN."""

from datetime import date

import pytest
from defusedxml import ElementTree

from app.core.errors import BusinessError
from app.core.registry import ACCOUNTANT, CASHIER
from app.features.org.models import Company
from app.features.payroll import htkk, service
from tests.conftest import ROLE_EMAILS
from tests.htkk_schema import validate
from tests.payroll.test_payroll import staff  # noqa: F401 - fixture

NS = {"t": "http://kekhaithue.gdt.gov.vn/TKhaiThue"}


def test_validation(db, ctxs, staff):  # noqa: F811
    ctx, a = ctxs[ACCOUNTANT], staff["a"]
    base = {"full_name": "Nguyễn Bé", "from_month": "2026-01"}
    with pytest.raises(BusinessError, match="giấy tờ"):
        service.add_dependent(db, ctx, a, base)
    with pytest.raises(BusinessError, match="10 hoặc 12"):
        service.add_dependent(db, ctx, a, base | {"tax_code": "123"})
    with pytest.raises(BusinessError, match="trước tháng"):
        service.add_dependent(
            db,
            ctx,
            a,
            base | {"id_number": "001", "to_month": "2025-12"},
        )
    with pytest.raises(BusinessError, match="tháng bắt đầu"):
        service.add_dependent(
            db, ctx, a, {"full_name": "X", "id_number": "001"}
        )
    d = service.add_dependent(db, ctx, a, base | {"id_number": "001234567890"})
    assert d.from_month == date(2026, 1, 1) and d.to_month is None
    assert service.active_dependents([d], 2026, 3) == 1
    assert service.active_dependents([d], 2025, 12) == 0


def test_appendix_05_3(db, ctxs, staff, company):  # noqa: F811
    ctx = ctxs[ACCOUNTANT]
    a, c = staff["a"], staff["c"]
    service.add_dependent(
        db,
        ctx,
        a,
        {
            "full_name": "Nguyễn Văn Bé",
            "birth_date": "2018-05-02",
            "relationship": "01",
            "id_type": "2080",
            "id_number": "001218000123",
            "from_month": "2025-01",
        },
    )
    # Ended before the year: not reported.
    service.add_dependent(
        db,
        ctx,
        a,
        {
            "full_name": "Trần Thị Mẹ",
            "relationship": "03",
            "tax_code": "8000000001",
            "from_month": "2024-01",
            "to_month": "2025-06",
        },
    )
    # The short-contract worker is not in 05-1: not reported.
    service.add_dependent(
        db,
        ctx,
        c,
        {"full_name": "Lê Con", "id_number": "1", "from_month": "2026-01"},
    )
    for month in (1, 2):
        service.post_run(db, ctx, service.create_run(db, ctx, 2026, month))
    data = htkk.build_xml(
        db, db.get(Company, company.id), 2026, signed_on=date(2027, 3, 1)
    )
    validate(data, "05_QTT_TNCN_TT80_293.xsd")
    root = ElementTree.fromstring(data)
    rows = root.findall(".//t:PLuc_05_3_BK_QTT/t:BKeTTinNPT", NS)
    assert len(rows) == 1
    r = rows[0]
    assert r.find("t:ct07", NS).text == "Nguyễn Văn An"
    assert r.find("t:ct09", NS).text == "Nguyễn Văn Bé"
    assert r.find("t:ct10", NS).text == "2018-05-02"
    assert r.find("t:ct12_ma", NS).text == "2080"
    assert r.find("t:ct14_ma", NS).text == "01"
    assert r.find("t:ct14_ten", NS).text == "Con"
    assert r.find("t:ct15", NS).text == "01/2026"
    assert r.find("t:ct16", NS).text == "12/2026"


def test_web(db, staff, web, users):  # noqa: F811
    a = staff["a"]
    web.login(ROLE_EMAILS[ACCOUNTANT])
    url = f"/payroll/employees/{a.id}"
    page = web.get(url).text
    assert 'id="dep-full_name"' in page and "05-3/BK-QTT-TNCN" in page
    r = web.post(
        url + "/dependents",
        {
            "full_name": "Nguyễn Văn Bé",
            "relationship": "01",
            "id_type": "2080",
            "id_number": "001218000123",
            "from_month": "2026-01",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    (d,) = service.list_dependents(db, a.company_id, a.id)
    assert "Nguyễn Văn Bé" in web.get(url).text
    web.login(ROLE_EMAILS[CASHIER])
    r = web.post(url + f"/dependents/{d.id}/delete", follow_redirects=False)
    assert r.status_code == 403
    web.login(ROLE_EMAILS[ACCOUNTANT])
    # The dependant must belong to the employee in the URL.
    other = staff["b"]
    r = web.post(
        f"/payroll/employees/{other.id}/dependents/{d.id}/delete",
        follow_redirects=False,
    )
    assert r.status_code in (303, 404)
    assert service.list_dependents(db, a.company_id, a.id)
    r = web.post(url + f"/dependents/{d.id}/delete", follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert service.list_dependents(db, a.company_id, a.id) == []
