"""OIDC ID-token verification."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient

from beacon_iam.errors import AuthenticationError

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True)
class OidcConfig:
    issuer: str
    client_id: str
    client_secret: str
    jwks_uri: str

    def __post_init__(self) -> None:
        for field_name in ("issuer", "client_id", "client_secret", "jwks_uri"):
            if not getattr(self, field_name):
                raise ValueError(f"OidcConfig.{field_name} is required")


@dataclass(frozen=True)
class OidcClaims:
    subject: str
    email: str
    name: str


class OidcVerifier:
    def __init__(self, config: OidcConfig) -> None:
        self.config = config
        self._jwks = PyJWKClient(config.jwks_uri)

    def verify_id_token(self, id_token: str) -> OidcClaims:
        """Validate an OIDC id_token against the issuer's JWKS and return its claims."""
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(id_token).key
            payload = cast(
                "Mapping[str, object]",
                jwt.decode(
                    id_token,
                    signing_key,
                    algorithms=["RS256"],
                    audience=self.config.client_id,
                    issuer=self.config.issuer,
                ),
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationError(f"OIDC token invalid: {exc}") from exc

        subject = payload["sub"]
        if not isinstance(subject, str):
            raise AuthenticationError("OIDC token subject is required")

        email = payload.get("email", "")
        if not isinstance(email, str):
            email = ""

        name = payload.get("name", email)
        if not isinstance(name, str):
            name = email

        return OidcClaims(subject=subject, email=email, name=name)


@dataclass(frozen=True)
class OidcAuthCodeClient:
    config: OidcConfig

    def authorize_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        scope: str = "openid email profile",
    ) -> str:
        """Build the OIDC authorization endpoint URL for the auth-code flow."""
        params = {
            "response_type": "code",
            "client_id": self.config.client_id,
            "redirect_uri": redirect_uri,
            "scope": scope,
            "state": state,
        }
        return f"{self.config.issuer.rstrip('/')}/protocol/openid-connect/auth?{urlencode(params)}"

    def exchange_code_for_id_token(self, *, code: str, redirect_uri: str) -> str:
        """Exchange an OIDC authorization code for an id_token at the token endpoint."""
        token_url = f"{self.config.issuer.rstrip('/')}/protocol/openid-connect/token"
        response = httpx.post(
            token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
            },
            timeout=15.0,
        )
        response.raise_for_status()
        body = cast("Mapping[str, object]", response.json())
        id_token = body.get("id_token")
        if not isinstance(id_token, str):
            raise AuthenticationError("OIDC token response missing id_token")
        return id_token

    @staticmethod
    def fresh_state() -> str:
        """Return a cryptographically random CSRF state token for the auth-code flow."""
        return secrets.token_urlsafe(32)
