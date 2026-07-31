import io
from pathlib import Path

import pytest
from beacon_storage.object_storage import LocalFsStorage, ObjectStorage


@pytest.fixture
def storage(tmp_path: Path) -> ObjectStorage:
    return LocalFsStorage(root=tmp_path)


def test_put_then_get_roundtrips(storage: ObjectStorage) -> None:
    storage.put("team=1/run=2/trace.json", io.BytesIO(b"hello"))
    out = storage.get("team=1/run=2/trace.json").read()
    assert out == b"hello"


def test_exists(storage: ObjectStorage) -> None:
    assert storage.exists("missing.txt") is False
    storage.put("present.txt", io.BytesIO(b"x"))
    assert storage.exists("present.txt") is True


def test_signed_url_for_local_returns_file_uri(storage: ObjectStorage) -> None:
    storage.put("a.txt", io.BytesIO(b"x"))
    url = storage.signed_url("a.txt", expires_seconds=60)
    assert url.startswith("file://")
    assert Path(url[7:]).exists()


def test_delete(storage: ObjectStorage) -> None:
    storage.put("d.txt", io.BytesIO(b"x"))
    storage.delete("d.txt")
    assert storage.exists("d.txt") is False
