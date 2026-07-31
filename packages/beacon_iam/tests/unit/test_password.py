from beacon_iam.auth.password import hash_password, verify_password


def test_hash_is_bcrypt_format() -> None:
    h = hash_password("hunter2!")
    assert h.startswith("$2b$") or h.startswith("$2a$")


def test_verify_correct_password() -> None:
    h = hash_password("hunter2!")
    assert verify_password("hunter2!", h) is True


def test_verify_wrong_password() -> None:
    h = hash_password("hunter2!")
    assert verify_password("wrong", h) is False


def test_hash_is_salted_so_two_hashes_differ() -> None:
    assert hash_password("same") != hash_password("same")
