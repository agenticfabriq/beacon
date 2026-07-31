from unittest.mock import Mock, patch

import jwt
import pytest
from beacon_iam.auth.oidc import OidcClaims, OidcConfig, OidcVerifier
from beacon_iam.errors import AuthenticationError


@pytest.fixture
def config() -> OidcConfig:
    return OidcConfig(
        issuer="https://idp.example.com",
        client_id="beacon",
        client_secret="secret",
        jwks_uri="https://example/jwks",
    )


def test_oidc_config_validates_required_fields() -> None:
    with pytest.raises(ValueError, match="issuer"):
        OidcConfig(issuer="", client_id="x", client_secret="y", jwks_uri="z")


@patch("beacon_iam.auth.oidc.jwt.decode")
@patch("beacon_iam.auth.oidc.PyJWKClient")
def test_verifier_extracts_claims(
    mock_jwks_cls: Mock, mock_decode: Mock, config: OidcConfig
) -> None:
    mock_jwks_cls.return_value.get_signing_key_from_jwt.return_value = Mock(key="key")
    mock_decode.return_value = {
        "sub": "user-42",
        "email": "alice@example.com",
        "name": "Alice",
        "iss": config.issuer,
        "aud": config.client_id,
    }

    verifier = OidcVerifier(config)
    claims = verifier.verify_id_token("dummy-jwt")

    assert isinstance(claims, OidcClaims)
    assert claims.subject == "user-42"
    assert claims.email == "alice@example.com"
    assert claims.name == "Alice"
    mock_decode.assert_called_once_with(
        "dummy-jwt",
        "key",
        algorithms=["RS256"],
        audience=config.client_id,
        issuer=config.issuer,
    )


@patch("beacon_iam.auth.oidc.jwt.decode")
@patch("beacon_iam.auth.oidc.PyJWKClient")
def test_verifier_maps_jwt_errors_to_authentication_error(
    mock_jwks_cls: Mock, mock_decode: Mock, config: OidcConfig
) -> None:
    mock_jwks_cls.return_value.get_signing_key_from_jwt.return_value = Mock(key="key")
    mock_decode.side_effect = jwt.InvalidTokenError("bad token")

    verifier = OidcVerifier(config)

    with pytest.raises(AuthenticationError, match="OIDC token invalid"):
        verifier.verify_id_token("dummy-jwt")
