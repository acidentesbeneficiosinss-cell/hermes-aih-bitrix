from __future__ import annotations

import pytest

from aih_bitrix.bitrix.fieldmap import FieldMapError, load_field_map
from aih_bitrix.config import ConfigError, Settings, load_env_file, load_settings
from fakes import FakeBitrix

TOKEN = "abcdef0123456789"
WEBHOOK = f"https://portal.bitrix24.com.br/rest/1/{TOKEN}/"


# ---------------------------------------------------------------- configuração

def test_settings_defaults_and_environment_parsing():
    env = {
        "BITRIX_WEBHOOK_URL": WEBHOOK, "HERMES_API_KEY": "chave", "OL_DIALOG_IDS": "chat1, chat2 ,",
        "MAX_PDF_MB": "20", "BITRIX_SPA_CATEGORY_ID": "101", "ALLOW_NO_AUTH": "true", "VERIFY_DELAY_SEC": "2.5",
    }
    s = load_settings(env)
    assert s.webhook_url == WEBHOOK and s.api_key == "chave" and s.ol_dialog_ids == ("chat1", "chat2")
    assert s.max_pdf_mb == 20 and s.category_id == 101 and s.allow_no_auth is True and s.verify_delay == 2.5
    d = load_settings({"BITRIX_WEBHOOK_URL": WEBHOOK})
    assert d.entity_type_id == 1146 and d.category_id is None and d.stage_id is None
    assert d.verify_delay == 8.0 and d.max_pdf_mb == 15 and d.allow_no_auth is False and d.poll_interval == 30
    assert d.title_template == "{nome} ➡ AIH {cid} ➡ {data_solicitacao}"


def test_env_file_key_value_lines_with_quotes_and_comments(tmp_path):
    f = tmp_path / ".env"
    f.write_text(f'# comentário\nBITRIX_WEBHOOK_URL="{WEBHOOK}"\n\nHERMES_API_KEY=abc\n', encoding="utf-8")
    assert load_env_file(f) == {"BITRIX_WEBHOOK_URL": WEBHOOK, "HERMES_API_KEY": "abc"}


def test_env_file_free_text_with_the_webhook_url_is_accepted(tmp_path):
    f = tmp_path / "bitrix.env.txt"  # formato do arquivo que o Windows/Notepad costuma produzir
    f.write_text(f"Seu webhook de entrada\n{WEBHOOK}\nou\n{WEBHOOK}profile.json\n", encoding="utf-8")
    assert load_env_file(f) == {"BITRIX_WEBHOOK_URL": WEBHOOK}


def test_env_file_utf16_with_bom_is_decoded(tmp_path):
    f = tmp_path / ".env"
    f.write_bytes(("BITRIX_WEBHOOK_URL=" + WEBHOOK + "\r\n").encode("utf-16"))
    assert load_env_file(f)["BITRIX_WEBHOOK_URL"] == WEBHOOK


def test_env_example_is_a_safe_template_for_the_cli_and_the_poller():
    from pathlib import Path

    template = Path(__file__).resolve().parent.parent / ".env.example"
    values = load_env_file(template)
    assert "HERMES_API_KEY" not in values  # uma chave de exemplo ativa viraria a chave real de quem copia o arquivo às cegas
    settings = load_settings({**values, "BITRIX_WEBHOOK_URL": WEBHOOK})  # só o webhook é preenchido pelo usuário
    assert settings.ol_dialog_ids == ("chat360293",) and settings.ledger_path == "data/ledger.sqlite3"


def test_environment_overrides_the_env_file(tmp_path):
    f = tmp_path / ".env"
    f.write_text(f"BITRIX_WEBHOOK_URL={WEBHOOK}\nHERMES_API_KEY=do-arquivo\n", encoding="utf-8")
    s = load_settings({"HERMES_API_KEY": "do-ambiente"}, env_file=f)
    assert s.api_key == "do-ambiente" and s.webhook_url == WEBHOOK


def test_missing_or_malformed_webhook_gives_a_clear_error_without_leaking_it():
    with pytest.raises(ConfigError, match="BITRIX_WEBHOOK_URL"):
        load_settings({})
    with pytest.raises(ConfigError) as info:
        load_settings({"BITRIX_WEBHOOK_URL": "https://portal.bitrix24.com.br/segredo-errado"})
    assert "segredo" not in str(info.value)


def test_invalid_number_names_the_variable():
    with pytest.raises(ConfigError, match="MAX_PDF_MB"):
        load_settings({"BITRIX_WEBHOOK_URL": WEBHOOK, "MAX_PDF_MB": "muito"})


# ---------------------------------------------------------------- mapa de campos (validado ao vivo)

def settings(**kw) -> Settings:
    return Settings(webhook_url=WEBHOOK, **kw)


def test_field_map_with_the_validated_defaults():
    fmap = load_field_map(FakeBitrix(), settings())
    assert (fmap.entity_type_id, fmap.category_id, fmap.stage_id) == (1146, 101, "DT1146_101:NEW")
    assert fmap.state_ids["MT"] == "377" and fmap.state_ids["SP"] == "5573"
    assert fmap.contact["cpf"] == "UF_CRM_1524762221558" and fmap.contact["state"] == "UF_CRM_1727815619072"
    assert fmap.item["cns"] == "ufCrm53_1792335030647" and fmap.item["surgery"] == "ufCrm53_1775651057904"


def test_missing_contact_field_is_reported_by_key():
    fake = FakeBitrix()
    del fake.contact_fields["UF_CRM_1725391645"]
    with pytest.raises(FieldMapError, match="UF_CRM_1725391645"):
        load_field_map(fake, settings())


def test_state_field_must_still_be_a_list():
    fake = FakeBitrix()
    fake.contact_fields["UF_CRM_1727815619072"]["type"] = "string"
    with pytest.raises(FieldMapError, match="UF_CRM_1727815619072"):
        load_field_map(fake, settings())


def test_spa_text_field_turned_into_another_type_is_refused():
    fake = FakeBitrix()
    fake.item_fields["ufCrm53_1775651041283"]["type"] = "date"
    with pytest.raises(FieldMapError, match="ufCrm53_1775651041283"):
        load_field_map(fake, settings())


def test_configured_category_and_stage_win_and_are_validated():
    fmap = load_field_map(FakeBitrix(), settings(category_id=101, stage_id="DT1146_101:CLIENT"))
    assert (fmap.category_id, fmap.stage_id) == (101, "DT1146_101:CLIENT")
    with pytest.raises(FieldMapError, match="999"):
        load_field_map(FakeBitrix(), settings(category_id=999))
    with pytest.raises(FieldMapError, match="DT1146_101:INEXISTENTE"):
        load_field_map(FakeBitrix(), settings(stage_id="DT1146_101:INEXISTENTE"))


def test_without_a_default_category_the_first_one_is_used():
    fake = FakeBitrix()
    fake.categories[0]["isDefault"] = "N"
    assert load_field_map(fake, settings()).category_id == 101
