import pytest

from app.core.money import (
    MoneyError,
    amount_in_words,
    format_vnd,
    number_to_words,
    parse_vnd,
)


@pytest.mark.parametrize(
    "amount, words",
    [
        # Required by section 8
        (10_000_000, "Mười triệu đồng chẵn"),
        (1_005_000, "Một triệu không trăm linh năm nghìn đồng"),
        (21_000, "Hai mươi mốt nghìn đồng"),
        (15_000, "Mười lăm nghìn đồng"),
        # Tricky cases from section 3.1
        (0, "Không đồng"),
        (1_005, "Một nghìn không trăm linh năm đồng"),
        (15_000_000, "Mười lăm triệu đồng chẵn"),
        (1_000_000_000, "Một tỷ đồng chẵn"),
        (21, "Hai mươi mốt đồng"),
        (105, "Một trăm linh năm đồng"),
    ],
)
def test_amount_in_words_required(amount, words):
    assert amount_in_words(amount) == words


@pytest.mark.parametrize(
    "n, words",
    [
        # "lăm" / "năm"
        (5, "năm"),
        (15, "mười lăm"),
        (25, "hai mươi lăm"),
        (50, "năm mươi"),
        (55, "năm mươi lăm"),
        (505, "năm trăm linh năm"),
        (5_000, "năm nghìn"),
        # "mốt" / "một"
        (1, "một"),
        (11, "mười một"),
        (21, "hai mươi mốt"),
        (101, "một trăm linh một"),
        (91, "chín mươi mốt"),
        (1_001, "một nghìn không trăm linh một"),
        # "linh" (default)
        (1_000_005, "một triệu không trăm linh năm"),
        (2_050_000, "hai triệu không trăm năm mươi nghìn"),
        (100_000_010, "một trăm triệu không trăm mười"),
        (1_000_000_000_005, "một nghìn tỷ không trăm linh năm"),
        (14, "mười bốn"),
        (110, "một trăm mười"),
        (1_100, "một nghìn một trăm"),
    ],
)
def test_number_to_words(n, words):
    assert number_to_words(n) == words


def test_le_variant():
    assert number_to_words(105, zero_tens="lẻ") == "một trăm lẻ năm"
    assert (
        amount_in_words(1_005_000, zero_tens="lẻ")
        == "Một triệu không trăm lẻ năm nghìn đồng"
    )


def test_format_and_parse():
    assert format_vnd(10_000_000) == "10.000.000"
    assert format_vnd(-3_000_000) == "-3.000.000"
    assert format_vnd(0) == "0"
    assert parse_vnd("10.000.000") == 10_000_000
    assert parse_vnd("10tr") == 10_000_000
    assert parse_vnd("1,5tr") == 1_500_000
    assert parse_vnd("500k") == 500_000
    assert parse_vnd("1 234 567") == 1_234_567


@pytest.mark.parametrize("bad", ["", "abc", "1.5", "10,5", "1,2345678tr"])
def test_parse_rejects_non_integer(bad):
    with pytest.raises(MoneyError):
        parse_vnd(bad)


def test_float_rejected():
    with pytest.raises(MoneyError):
        format_vnd(1.5)
    with pytest.raises(MoneyError):
        parse_vnd(1.5)
