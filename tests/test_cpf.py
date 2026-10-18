from __future__ import annotations

from aih_bitrix.cpf import (
    complete_cpf,
    cpf_from_filename,
    cpf_from_text,
    format_cpf,
    is_valid_cpf,
    mask_cpf,
    mask_cpfs_in_text,
    only_digits,
)


def test_only_digits_strips_everything_else():
    assert only_digits("123.456.789-09") == "12345678909"
    assert only_digits(None) == ""


def test_valid_cpf_accepts_formatted_and_plain():
    assert is_valid_cpf("12345678909")
    assert is_valid_cpf("123.456.789-09")


def test_invalid_cpf_wrong_check_digit_repeated_or_short():
    assert not is_valid_cpf("12345678905")
    assert not is_valid_cpf("11111111111")
    assert not is_valid_cpf("123")
    assert not is_valid_cpf(None)


def test_format_cpf_uses_the_portal_storage_style():
    assert format_cpf("12345678909") == "123.456.789-09"


def test_mask_cpf_keeps_only_last_two_digits():
    assert mask_cpf("123.456.789-09") == "***.***.***-09"


def test_complete_cpf_appends_the_two_check_digits():
    assert complete_cpf("123456789") == "12345678909"
    assert complete_cpf("987654321") == "98765432100"
    assert is_valid_cpf(complete_cpf("111222333")) and complete_cpf("111222333") == "11122233396"


def test_mask_cpfs_in_text_hides_valid_cpfs_only():
    assert mask_cpfs_in_text("CPF12345678909.pdf") == "CPF***.***.***-09.pdf"
    assert mask_cpfs_in_text("cpf 123.456.789-09 e 12345678900") == "cpf ***.***.***-09 e 12345678900"
    assert mask_cpfs_in_text("") == ""


def test_cpf_from_filename_variants():
    assert cpf_from_filename("CPF12345678909.pdf") == "12345678909"
    assert cpf_from_filename("cpf 123.456.789-09 AIH.pdf") == "12345678909"
    assert cpf_from_filename("CPF_12345678909_laudo.PDF") == "12345678909"


def test_cpf_from_filename_rejects_names_without_valid_cpf():
    assert cpf_from_filename("MARCOS DE OLIVEIRA, AIH .PDF") is None
    assert cpf_from_filename("CPF12345678905.pdf") is None


def test_cpf_from_text_reads_chat_message_style():
    assert cpf_from_text("cpf 123.456.789-09") == "12345678909"
    assert cpf_from_text("CPF: 12345678909") == "12345678909"
    assert cpf_from_text("-------------") is None
    assert cpf_from_text("") is None
