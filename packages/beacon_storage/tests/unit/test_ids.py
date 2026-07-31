import time
from uuid import UUID

from beacon_storage.ids import uuid7, uuid7_str


def test_uuid7_returns_uuid_instance() -> None:
    assert isinstance(uuid7(), UUID)


def test_uuid7_is_time_sortable() -> None:
    a = uuid7()
    time.sleep(0.005)
    b = uuid7()
    assert str(a) < str(b)


def test_uuid7_str_is_string() -> None:
    assert isinstance(uuid7_str(), str)
    assert len(uuid7_str()) == 36
