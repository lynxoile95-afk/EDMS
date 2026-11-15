"""Pluggable authentication providers.

Phase 1 ships a local (password hash in DB) provider.  LDAP/AD and OIDC
(Keycloak) providers are stubs with a precise contract so they can be added
without touching the API layer:

    provider.authenticate(identifier: str, secret: str) -> ExternalIdentity | None

`ExternalIdentity` carries the fields we persist on :class:`app.models.User`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.core.config import settings
from app.core.security import verify_password


@dataclass(slots=True)
class ExternalIdentity:
    email: str
    full_name: str
    external_id: str | None = None
    provider: str = "local"


class AuthProvider(Protocol):
    name: str

    def authenticate(self, identifier: str, secret: str) -> ExternalIdentity | None: ...


class LocalAuthProvider:
    """Validates against the `users.hashed_password` column."""

    name = "local"

    def __init__(self, user) -> None:  # user: app.models.User
        self.user = user

    def authenticate(self, identifier: str, secret: str) -> ExternalIdentity | None:
        if self.user is None or not self.user.is_active or not self.user.hashed_password:
            return None
        if self.user.email.lower() != identifier.lower():
            return None
        if not verify_password(secret, self.user.hashed_password):
            return None
        return ExternalIdentity(
            email=self.user.email,
            full_name=self.user.full_name,
            external_id=self.user.external_id,
            provider="local",
        )


class LdapAuthProvider:
    """Active Directory / LDAP bind.

    Implemented in phase 2 (``ldap3``). The contract is fixed: bind with the
    supplied credentials, read `mail`/`displayName`, return None on failure.
    """

    name = "ldap"

    def authenticate(self, identifier: str, secret: str) -> ExternalIdentity | None:  # pragma: no cover
        raise NotImplementedError(
            "LDAP provider is not enabled in phase 1. "
            "Set AUTH_PROVIDER=local or implement LdapAuthProvider.authenticate()."
        )


class OidcAuthProvider:
    """Keycloak / OIDC authorization-code flow.

    In this mode the backend validates a provider-issued ID token instead of
    accepting a password (see `services.auth`). Kept as an explicit stub so the
    switch is a configuration change, not a refactor.
    """

    name = "oidc"

    def authenticate(self, identifier: str, secret: str) -> ExternalIdentity | None:  # pragma: no cover
        raise NotImplementedError(
            "OIDC provider is not enabled in phase 1. "
            "Validate the ID token in services.auth.authenticate_oidc() instead."
        )


def get_provider(name: str | None = None) -> type[LocalAuthProvider] | type[LdapAuthProvider] | type[OidcAuthProvider]:
    name = (name or settings.auth_provider).lower()
    return {"local": LocalAuthProvider, "ldap": LdapAuthProvider, "oidc": OidcAuthProvider}[name]
