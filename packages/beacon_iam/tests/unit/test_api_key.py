from beacon_iam.auth.api_key import generate_api_key, hash_api_key, verify_api_key


def test_generate_starts_with_prefix() -> None:
    key = generate_api_key(prefix="bcn_dev")
    assert key.startswith("bcn_dev_")
    assert len(key) > len("bcn_dev_") + 32


def test_hash_is_deterministic_for_same_key() -> None:
    key = "bcn_dev_abc123"
    assert hash_api_key(key) == hash_api_key(key)


def test_hash_differs_across_keys() -> None:
    assert hash_api_key("bcn_dev_a") != hash_api_key("bcn_dev_b")


def test_verify_round_trip() -> None:
    key = generate_api_key(prefix="bcn_dev")
    h = hash_api_key(key)
    assert verify_api_key(key, h) is True
    assert verify_api_key("bcn_dev_wrong", h) is False
