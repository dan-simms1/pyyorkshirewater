"""Authentication against Yorkshire Water's IdentityServer.

The SPA OAuth client (`css-onlineaccount-fe`) only allows
`authorization_code` with PKCE. ROPC, device flow and `offline_access` are
all rejected. The library cannot drive a normal redirect flow because the
allowed `redirect_uri` is `https://my.yorkshirewater.com/account/callback/response`
which Home Assistant cannot intercept, and the login form is protected by
invisible Google reCAPTCHA v3 which a non-browser HTTP client cannot pass.

The strategy this module implements: the user logs in once via their own
browser (where reCAPTCHA passes naturally) and exports the IdentityServer
session cookie. The library uses that cookie to drive
`/connect/authorize?prompt=none&...` for silent renewal, capturing the
authorization code from the redirect Location header and exchanging it for
an access token at `/connect/token` with a fresh PKCE verifier.

When the IdP session cookie eventually expires the user re-exports cookies.
The companion Chrome extension makes that one click. There is no refresh
token because `offline_access` is not allowed for this client.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from .const import (
    AUTHORIZE_ENDPOINT,
    DEFAULT_CLIENT_ID,
    DEFAULT_REDIRECT_URI,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    DEFAULT_SCOPES,
    DEFAULT_USER_AGENT,
    IDP_BASE_URL,
    IDP_COOKIE_DOMAIN,
    PACKAGE_NAME,
    PKCE_VERIFIER_BYTES,
    TOKEN_ENDPOINT,
    TOKEN_REFRESH_LEEWAY_SECONDS,
)
from .exceptions import YorkshireWaterAuthError
from .models import TokenSet

_LOGGER = logging.getLogger(PACKAGE_NAME)


class CookieSessionExpiredError(YorkshireWaterAuthError):
    """The IdP session cookie is expired, missing or invalid.

    Raised when `/connect/authorize?prompt=none` returns
    `error=login_required` (or the equivalent). The caller must obtain fresh
    cookies from a user-driven browser login and reconfigure the client.
    """


class Authenticator:
    """Owns OAuth state for a single account using cookie-based silent renewal.

    The constructor accepts a flat dict of cookie name to value. The cookies
    are scoped to `login.yorkshirewater.com` (the IdP). Other domains' cookies
    are not used by this library.

    The instance is one-shot from the IdP's perspective: silent renewal is
    initiated whenever a fresh access token is needed. There is no refresh
    token because the SPA client is not allowed `offline_access`.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        cookies: dict[str, str],
        client_id: str = DEFAULT_CLIENT_ID,
        scopes: str = DEFAULT_SCOPES,
        redirect_uri: str = DEFAULT_REDIRECT_URI,
    ) -> None:
        """Configure the authenticator.

        Args:
            http_client: Shared async HTTP client. Caller owns its lifecycle.
            cookies: Mapping of cookie names to values harvested from the user's
                logged-in browser session at `login.yorkshirewater.com`. At
                minimum this should include the IdentityServer session cookie
                (typically `idsrv` and `idsrv.session`).
            client_id: OAuth client id. Defaults to the SPA value.
            scopes: Space-separated scope string. Must match the SPA's scopes
                because the IdP enforces per-client scope policies.
            redirect_uri: Authorization callback URI that the SPA client is
                registered with. The library never visits this URL: it only
                parses the authorization code out of the redirect Location.
        """
        if not cookies:
            raise YorkshireWaterAuthError(
                "No cookies were supplied. Export the IdentityServer session "
                "cookies from a logged-in browser session at "
                "https://my.yorkshirewater.com and pass them in.",
            )

        self._http = http_client
        self._cookies = dict(cookies)
        self._client_id = client_id
        self._scopes = scopes
        self._redirect_uri = redirect_uri
        self._tokens: TokenSet | None = None
        self._lock = asyncio.Lock()

    @property
    def access_token(self) -> str | None:
        """Return the current access token, or None if not authenticated."""
        return self._tokens.access_token if self._tokens else None

    @property
    def is_authenticated(self) -> bool:
        """Return True if a non-expired access token is held."""
        return self._tokens is not None and not self._tokens.is_expired()

    @property
    def cookies(self) -> dict[str, str]:
        """Return a copy of the IdP cookies the authenticator is using."""
        return dict(self._cookies)

    async def login(self) -> TokenSet:
        """Acquire a fresh access token via silent renewal."""
        async with self._lock:
            self._tokens = await self._silent_renewal()
            return self._tokens

    async def ensure_token(self) -> str:
        """Return a valid access token, renewing if required."""
        if self._tokens and not self._tokens.is_expired(TOKEN_REFRESH_LEEWAY_SECONDS):
            return self._tokens.access_token

        async with self._lock:
            # Re-check inside the lock to avoid a thundering renewal.
            if self._tokens and not self._tokens.is_expired(TOKEN_REFRESH_LEEWAY_SECONDS):
                return self._tokens.access_token
            self._tokens = await self._silent_renewal()
            return self._tokens.access_token

    async def force_refresh(self) -> str:
        """Force a silent renewal regardless of token expiry."""
        async with self._lock:
            self._tokens = await self._silent_renewal()
            return self._tokens.access_token

    async def revoke(self) -> None:
        """Drop local token state.

        There is no server-side revocation we can perform without a refresh
        token (which we do not have). Logging the user out of their browser
        session is what the user must do to invalidate the IdP session
        cookie. This method clears our in-memory token cache.
        """
        self._tokens = None

    async def _silent_renewal(self) -> TokenSet:
        """Drive `/connect/authorize?prompt=none` then exchange the code."""
        verifier = self._generate_pkce_verifier()
        challenge = self._pkce_challenge(verifier)
        state = secrets.token_urlsafe(16)

        params = {
            "response_type": "code",
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "scope": self._scopes,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "prompt": "none",
        }

        try:
            authorize_response = await self._http.get(
                AUTHORIZE_ENDPOINT,
                params=params,
                cookies=self._cookies,
                headers=self._common_headers(),
                timeout=DEFAULT_REQUEST_TIMEOUT_SECONDS,
                follow_redirects=False,
            )
        except httpx.HTTPError as err:
            raise YorkshireWaterAuthError(
                f"Network error during silent renewal: {err}",
            ) from err

        # IdentityServer rotates the .AspNetCore.Identity.Application
        # cookie on every silent renewal. The old value becomes invalid
        # within seconds. Capture any Set-Cookie headers the IdP returned
        # so the next call uses the rotated value, and so callers can
        # persist them.
        self._absorb_set_cookies(authorize_response)

        code = self._extract_code(authorize_response, expected_state=state)
        tokens = await self._exchange_code(code=code, verifier=verifier)
        return tokens

    def _absorb_set_cookies(self, response: httpx.Response) -> None:
        """Update self._cookies with anything the IdP rotated in this response."""
        for name, value in response.cookies.items():
            self._cookies[name] = value

    def _extract_code(
        self,
        response: httpx.Response,
        *,
        expected_state: str,
    ) -> str:
        """Pull the authorization `code` out of the redirect Location header."""
        if response.status_code not in (302, 303):
            # The IdP redirects on success, on error and on login_required. A
            # non-redirect response usually means we hit the IdP's HTML error
            # page directly.
            raise YorkshireWaterAuthError(
                f"Authorize endpoint returned HTTP {response.status_code}, "
                "expected a redirect.",
                status_code=response.status_code,
            )

        location = response.headers.get("location")
        if not location:
            raise YorkshireWaterAuthError(
                "Authorize endpoint returned a redirect with no Location header.",
            )

        parsed = urlparse(location)

        # Defence in depth: require the redirect to point at the URI we
        # registered with the IdP. A redirect to anywhere else is either an
        # IdP misconfiguration or a tampered response, and we should not
        # treat any code or error in such a redirect as authoritative.
        self._validate_redirect_target(parsed)

        params = parse_qs(parsed.query)

        # RFC 9207: if the IdP echoes an `iss` parameter in the redirect, it
        # must equal our IdP issuer. The Yorkshire Water IdP advertises
        # `authorization_response_iss_parameter_supported`, so the parameter
        # may or may not be present. When present we enforce it.
        self._validate_iss_parameter(params)

        if "error" in params:
            error = params["error"][0]
            description = params.get("error_description", [None])[0]
            if error == "login_required":
                raise CookieSessionExpiredError(
                    "Silent renewal returned login_required. The IdP session "
                    "cookie is expired or invalid. Export fresh cookies from "
                    "a logged-in browser session.",
                    error=error,
                    error_description=description,
                )
            raise YorkshireWaterAuthError(
                f"Silent renewal failed: error={error}",
                error=error,
                error_description=description,
            )

        if "state" not in params:
            raise YorkshireWaterAuthError(
                "Authorize redirect did not echo the state parameter.",
            )
        if not hmac.compare_digest(params["state"][0], expected_state):
            raise YorkshireWaterAuthError(
                "State mismatch during silent renewal. Possible CSRF.",
            )

        if "code" not in params:
            raise YorkshireWaterAuthError(
                "Silent renewal response had no code parameter.",
            )

        code: str = str(params["code"][0])
        return code

    def _validate_redirect_target(self, parsed_location: Any) -> None:
        """Reject any redirect whose target does not match our registered URI."""
        registered = urlparse(self._redirect_uri)
        # scheme and host must match exactly. Path must match too because
        # IdentityServer enforces full-string redirect URI equality.
        if (
            parsed_location.scheme != registered.scheme
            or parsed_location.hostname != registered.hostname
            or parsed_location.path != registered.path
        ):
            raise YorkshireWaterAuthError(
                "Authorize redirect pointed at an unexpected target. "
                "Refusing to trust the response.",
            )

    @staticmethod
    def _validate_iss_parameter(params: dict[str, list[str]]) -> None:
        """If `iss` is present, it must equal the configured IdP issuer."""
        if "iss" not in params:
            return
        actual = params["iss"][0]
        if not hmac.compare_digest(actual, IDP_BASE_URL):
            raise YorkshireWaterAuthError(
                "Authorize redirect carried an iss parameter that does not "
                "match the configured IdP issuer.",
            )

    async def _exchange_code(self, *, code: str, verifier: str) -> TokenSet:
        """Exchange an authorization code for an access token at /connect/token."""
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "code_verifier": verifier,
        }

        try:
            response = await self._http.post(
                TOKEN_ENDPOINT,
                data=data,
                headers=self._common_headers(),
                cookies=self._cookies,
                timeout=DEFAULT_REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as err:
            raise YorkshireWaterAuthError(
                f"Network error during token exchange: {err}",
            ) from err

        if response.status_code >= 400:
            self._raise_token_error(response)

        # The token endpoint may also rotate cookies. Capture any.
        self._absorb_set_cookies(response)

        try:
            payload: dict[str, Any] = response.json()
        except ValueError as err:
            raise YorkshireWaterAuthError(
                "Token endpoint returned a non-JSON response.",
                status_code=response.status_code,
            ) from err

        return self._token_set_from_payload(payload)

    @staticmethod
    def _generate_pkce_verifier() -> str:
        """Generate a fresh PKCE code_verifier per RFC 7636."""
        return secrets.token_urlsafe(PKCE_VERIFIER_BYTES)

    @staticmethod
    def _pkce_challenge(verifier: str) -> str:
        """Compute the PKCE S256 code_challenge from a verifier."""
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    @staticmethod
    def _common_headers() -> dict[str, str]:
        return {
            "Accept": "application/json",
            "User-Agent": DEFAULT_USER_AGENT,
        }

    @staticmethod
    def _raise_token_error(response: httpx.Response) -> None:
        error: str | None = None
        description: str | None = None
        try:
            payload = response.json()
            if isinstance(payload, dict):
                error = payload.get("error")
                description = payload.get("error_description")
        except ValueError:
            pass

        message_parts = ["Token exchange failed"]
        if error:
            message_parts.append(f"error={error}")
        if description:
            message_parts.append(f"description={description}")
        message_parts.append(f"status={response.status_code}")
        raise YorkshireWaterAuthError(
            "; ".join(message_parts),
            error=error,
            error_description=description,
            status_code=response.status_code,
        )

    @staticmethod
    def _token_set_from_payload(payload: dict[str, Any]) -> TokenSet:
        try:
            access_token = str(payload["access_token"])
            expires_in = int(payload["expires_in"])
            token_type = str(payload.get("token_type", "Bearer"))
        except (KeyError, TypeError, ValueError) as err:
            raise YorkshireWaterAuthError(
                "Token endpoint response was missing required fields.",
            ) from err

        scope = payload.get("scope")
        expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)

        # The SPA client does not get a refresh_token because offline_access
        # is not allowed. We construct a TokenSet with refresh_token=None to
        # keep the data shape uniform.
        return TokenSet(
            access_token=access_token,
            refresh_token=None,
            token_type=token_type,
            expires_at=expires_at,
            scope=str(scope) if scope else None,
        )


def cookies_required_for_idp(cookies: dict[str, str]) -> dict[str, str]:
    """Filter a cookie dict to only those scoped to the IdP host.

    The library does not itself enforce a scope; httpx will not send a cookie
    where the domain does not match. This helper exists to give callers a
    way to keep their stored cookie set focused.

    The resulting cookies are still passed as a flat dict; httpx assigns them
    to whatever host the request is going to. That is fine here because all
    auth requests target `login.yorkshirewater.com`.
    """
    # Without metadata in the input, we cannot truly filter by domain. The
    # function is a placeholder that returns the input unchanged. The Chrome
    # extension is responsible for capturing only IDP cookies.
    return dict(cookies)


__all__ = [
    "IDP_COOKIE_DOMAIN",
    "Authenticator",
    "CookieSessionExpiredError",
    "cookies_required_for_idp",
]
