"""
Tests for folder auto-watch (memory/folder_watch.py) and its SQLite CRUD
(memory/structured.py's watched_folders/watched_files tables).

Deliberately does not test live watchdog filesystem events here — real OS
event delivery timing is flaky in CI, especially on Windows. The
deterministic scan-based functions (add_folder's initial scan, reconciliation,
remove_folder's cleanup) are what's tested; live-event behavior is verified
manually against the running server (see the Phase 4 plan's verification
steps).

Run with: pytest tests/
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory import folder_watch, structured


def _wait_for_scan(folder_id: int, expected_count: int, timeout: float = 5.0) -> list[dict]:
    """add_folder's initial scan runs in a background thread — poll briefly
    instead of assuming it finished by the time add_folder() returns."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        files = structured.list_watched_files(folder_id)
        if len(files) >= expected_count:
            return files
        time.sleep(0.1)
    return structured.list_watched_files(folder_id)


def test_watched_folder_crud_round_trip():
    folder_id = structured.add_watched_folder("C:\\fake\\path\\for\\crud\\test")
    try:
        listed = structured.list_watched_folders()
        assert any(f["id"] == folder_id for f in listed)
        assert structured.get_watched_folder(folder_id)["path"] == "C:\\fake\\path\\for\\crud\\test"
    finally:
        assert structured.delete_watched_folder(folder_id) is True
    assert structured.get_watched_folder(folder_id) is None


def test_watched_file_upsert_and_delete():
    folder_id = structured.add_watched_folder("C:\\fake\\path\\for\\file\\test")
    try:
        structured.upsert_watched_file(folder_id, "C:\\fake\\path\\for\\file\\test\\a.txt", 123, 1000.0)
        row = structured.get_watched_file("C:\\fake\\path\\for\\file\\test\\a.txt")
        assert row["document_id"] == 123
        assert row["mtime"] == 1000.0

        # Upserting the same filepath again updates in place, not duplicates.
        structured.upsert_watched_file(folder_id, "C:\\fake\\path\\for\\file\\test\\a.txt", 456, 2000.0)
        row = structured.get_watched_file("C:\\fake\\path\\for\\file\\test\\a.txt")
        assert row["document_id"] == 456
        assert len(structured.list_watched_files(folder_id)) == 1

        deleted = structured.delete_watched_file("C:\\fake\\path\\for\\file\\test\\a.txt")
        assert deleted["document_id"] == 456
        assert structured.get_watched_file("C:\\fake\\path\\for\\file\\test\\a.txt") is None
    finally:
        structured.delete_watched_folder(folder_id)


def test_add_folder_ingests_existing_files(tmp_path):
    (tmp_path / "notes.txt").write_text("The dungeon key is under the third floorboard.")
    (tmp_path / "ignored.bin").write_bytes(b"\x00\x01\x02")  # unsupported extension, skipped

    folder = folder_watch.add_folder(str(tmp_path))
    try:
        files = _wait_for_scan(folder["id"], expected_count=1)
        assert len(files) == 1
        assert files[0]["filepath"].endswith("notes.txt")

        docs = structured.list_documents()
        doc = next(d for d in docs if d["id"] == files[0]["document_id"])
        assert doc["source_type"] == "folder"

        from memory import knowledge

        results = knowledge.search("where is the dungeon key")
        assert any("floorboard" in r["text"] for r in results)
    finally:
        folder_watch.remove_folder(folder["id"])


def test_add_folder_rejects_nonexistent_path(tmp_path):
    missing = tmp_path / "does_not_exist"
    try:
        folder_watch.add_folder(str(missing))
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_add_folder_rejects_duplicate_watch(tmp_path):
    folder = folder_watch.add_folder(str(tmp_path))
    try:
        try:
            folder_watch.add_folder(str(tmp_path))
            assert False, "expected ValueError"
        except ValueError:
            pass
    finally:
        folder_watch.remove_folder(folder["id"])


def test_remove_folder_deletes_its_documents(tmp_path):
    (tmp_path / "secret.txt").write_text("The password is hunter2.")

    folder = folder_watch.add_folder(str(tmp_path))
    files = _wait_for_scan(folder["id"], expected_count=1)
    document_id = files[0]["document_id"]

    assert folder_watch.remove_folder(folder["id"]) is True
    assert structured.get_watched_folder(folder["id"]) is None
    assert not any(d["id"] == document_id for d in structured.list_documents())


def test_scan_folder_reingests_changed_file(tmp_path):
    path = tmp_path / "doc.txt"
    path.write_text("Version one of the plan.")

    folder = folder_watch.add_folder(str(tmp_path))
    try:
        files = _wait_for_scan(folder["id"], expected_count=1)
        original_document_id = files[0]["document_id"]

        # Bump the mtime so the scan sees it as changed, then rescan directly
        # (bypassing the debounced live-watcher path, which this test suite
        # deliberately doesn't rely on).
        time.sleep(0.05)
        path.write_text("Version two of the plan, revised.")
        folder_watch._scan_folder(folder["id"], tmp_path)

        updated = structured.get_watched_file(str(path))
        assert updated["document_id"] != original_document_id
        assert not any(d["id"] == original_document_id for d in structured.list_documents())

        from memory import knowledge

        results = knowledge.search("what does the revised plan say")
        assert any("revised" in r["text"] for r in results)
    finally:
        folder_watch.remove_folder(folder["id"])


def test_scan_folder_removes_deleted_file(tmp_path):
    path = tmp_path / "temp.txt"
    path.write_text("Ephemeral content.")

    folder = folder_watch.add_folder(str(tmp_path))
    try:
        files = _wait_for_scan(folder["id"], expected_count=1)
        document_id = files[0]["document_id"]

        path.unlink()
        folder_watch._scan_folder(folder["id"], tmp_path)

        assert structured.get_watched_file(str(path)) is None
        assert not any(d["id"] == document_id for d in structured.list_documents())
    finally:
        folder_watch.remove_folder(folder["id"])
