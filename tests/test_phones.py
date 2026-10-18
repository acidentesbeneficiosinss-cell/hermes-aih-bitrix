from __future__ import annotations

import pytest

from aih_bitrix.phones import (
    format_phone_br,
    is_placeholder_phone,
    phone_digits,
    phone_key,
    same_phone,
)


def test_phone_digits():
    assert phone_digits("(65) 99123-4567") == "65991234567"
    assert phone_digits(None) == ""


@pytest.mark.parametrize("value", ["(65) 999999999", "(65) 90000-0000", "(65) 912345678", "(65) 987654321", "", None, "12345"])
def test_placeholder_phones(value):
    assert is_placeholder_phone(value)


def test_real_phone_is_not_placeholder():
    assert not is_placeholder_phone("(65) 99123-4567")
    assert not is_placeholder_phone("+55 65 3321-4567")


def test_variants_of_the_same_number_share_the_key():
    variants = ["(65) 99123-4567", "65991234567", "+55 65 99123-4567", "5565991234567", "556591234567", "6591234567"]
    assert {phone_key(v) for v in variants} == {"6591234567"}


def test_phone_key_empty_when_there_is_no_ddd_and_number():
    assert phone_key("91234567") == ""
    assert phone_key(None) == ""


def test_same_phone_matches_variants_and_rejects_different_numbers():
    assert same_phone("(65) 99123-4567", "5565991234567")
    assert not same_phone("(65) 99123-4567", "(65) 99123-4568")
    assert not same_phone("(65) 99123-4567", "(11) 99123-4567")


def test_same_phone_without_ddd_compares_the_last_eight_digits():
    assert same_phone("91234567", "(65) 99123-4567")


def test_format_phone_br():
    assert format_phone_br("5565991234567") == "(65) 99123-4567"
    assert format_phone_br("6591234567") == "(65) 9123-4567"
    assert format_phone_br("12345") == "12345"
