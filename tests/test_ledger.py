from __future__ import annotations

from aih_bitrix.ledger import Ledger


def test_only_processed_and_non_aih_files_block_reprocessing():
    ledger = Ledger(":memory:")
    assert not ledger.seen("a")
    ledger.record("a", status="ok", source="upload", contact_id=1, item_id=2)
    ledger.record("b", status="not_aih", source="poller")
    ledger.record("c", status="needs_review", source="upload", contact_id=3)
    ledger.record("d", status="error", source="upload")
    assert ledger.seen("a") and ledger.seen("b")
    assert not ledger.seen("c") and not ledger.seen("d")  # podem ser reprocessados depois de corrigidos


def test_record_updates_the_same_row_and_get_returns_the_ids():
    ledger = Ledger(":memory:")
    ledger.record("x", status="needs_review", source="upload", contact_id=7)
    ledger.record("x", status="ok", source="upload", contact_id=7, item_id=9)
    row = ledger.get("x")
    assert row["status"] == "ok" and row["contact_id"] == 7 and row["item_id"] == 9
    assert ledger.get("nao-existe") is None


def test_cursor_defaults_to_zero_and_only_moves_forward():
    ledger = Ledger(":memory:")
    assert ledger.get_cursor("chat1") == 0
    ledger.set_cursor("chat1", 10)
    ledger.set_cursor("chat1", 5)
    ledger.set_cursor("chat2", 3)
    assert ledger.get_cursor("chat1") == 10 and ledger.get_cursor("chat2") == 3


def test_ledger_persists_on_disk_and_creates_parent_folders(tmp_path):
    path = tmp_path / "dados" / "ledger.sqlite3"
    first = Ledger(path)
    first.record("h", status="ok", source="upload")
    first.set_cursor("chat9", 42)
    second = Ledger(path)
    assert second.seen("h") and second.get_cursor("chat9") == 42


def test_the_ledger_never_stores_file_names_or_personal_data():
    ledger = Ledger(":memory:")
    ledger.record("h", status="ok", source="upload", contact_id=1, item_id=2)
    assert set(ledger.get("h")) == {"sha256", "status", "source", "contact_id", "item_id", "detail", "created_at", "updated_at"}
