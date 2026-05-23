import sqlite3
import sys
import tarfile
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.quant.data_transfer import import_data_package


def _write_db(path: Path, rows: list[tuple[int, str]]) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE sample_rows (id INTEGER PRIMARY KEY, value TEXT)")
        conn.executemany("INSERT INTO sample_rows (id, value) VALUES (?, ?)", rows)
        conn.commit()
    finally:
        conn.close()


def test_import_data_package_streams_and_merges_sqlite(tmp_path):
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    _write_db(target_dir / "quant_data.sqlite3", [(1, "old")])

    incoming_db = tmp_path / "incoming.sqlite3"
    _write_db(incoming_db, [(1, "new"), (2, "added")])
    package_file = tmp_path / "package.tar.gz"
    with tarfile.open(package_file, "w:gz") as archive:
        archive.add(incoming_db, arcname="backend/data/quant_data.sqlite3")

    result = import_data_package(package_file, target_dir)

    conn = sqlite3.connect(target_dir / "quant_data.sqlite3")
    try:
        rows = conn.execute("SELECT id, value FROM sample_rows ORDER BY id").fetchall()
    finally:
        conn.close()

    assert result["status"] == "ok"
    assert result["merge_actions"] == {"merged": 1}
    assert rows == [(1, "new"), (2, "added")]
