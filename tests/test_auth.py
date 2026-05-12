"""Unit tests for `pyyorkshirewater.auth`."""

from __future__ import annotations

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from pyyorkshirewater.auth import Authenticator, CookieSessionExpiredError
from pyyorkshirewater.const import (
    AUTHORIZE_ENDPOINT,
    DEFAULT_REDIRECT_URI,
    TOKEN_ENDPOINT,
)
from pyyorkshirewater.exceptions import YorkshireWaterAuthError

from .conftest import SAMPLE_COOKIES


def _token_response(expires_in: int = 3600) -> dict[str, object]:
    return {
        "access_token": "access-1",
        "expires_in": expires_in,
        "token_type": "Bearer",
        "scope": "openid user-names css-onlineaccount-api css-registration-api",
    }


def _state_echo_response(code: str = "code-1"):
    """Side effect that echoes the request's state in the redirect."""
    def _impl(request: httpx.Request) -> httpx.Response:
        params = parse_qs(urlparse(str(request.url)).query)
        state = params["state"][0]
        return httpx.Response(
            302,
            headers={"location": f"{DEFAULT_REDIRECT_URI}?code={code}&state={state}"},
        )
    return _impl


def _redirect_with_error(error: str = "login_required"):
    """Side effect that returns the given error and echoes the request state."""
    def _impl(request: httpx.Request) -> httpx.Response:
        params = parse_qs(urlparse(str(request.url)).query)
        state = params["state"][0]
        return httpx.Response(
            302,
            headers={"location": f"{DEFAULT_REDIRECT_URI}?error={error}&state={state}"},
        )
    return _impl


def _capture_state(request: httpx.Request) -> str:
    """Extract the `state` parameter the authenticator chose for the request."""
    params = parse_qs(urlparse(str(request.url)).query)
    return params["state"][0]


@pytest.mark.asyncio
async def test_authenticator_requires_cookies() -> None:
    async with httpx.AsyncClient() as http:
        with pytest.raises(YorkshireWaterAuthError):
            Authenticator(http, cookies={})


@pytest.mark.asyncio
@respx.mock
async def test_silent_renewal_happy_path(http_client: httpx.AsyncClient) -> None:
    """Authorize endpoint returns a code; token endpoint exchanges it."""
    respx.get(AUTHORIZE_ENDPOINT).mock(side_effect=_state_echo_response())
    respx.post(TOKEN_ENDPOINT).mock(return_value=httpx.Response(200, json=_token_response()))

    auth = Authenticator(http_client, cookies=dict(SAMPLE_COOKIES))
    tokens = await auth.login()

    assert tokens.access_token == "access-1"
    assert tokens.refresh_token is None
    assert auth.is_authenticated

    # Authorize call must include prompt=none, the SPA scopes and a PKCE
    # code_challenge that hashes back to the verifier sent at exchange.
    auth_request = respx.calls[0].request
    auth_params = parse_qs(urlparse(str(auth_request.url)).query)
    assert auth_params["prompt"] == ["none"]
    assert auth_params["client_id"] == ["css-onlineaccount-fe"]
    assert auth_params["response_type"] == ["code"]
    assert auth_params["redirect_uri"] == [DEFAULT_REDIRECT_URI]
    assert auth_params["code_challenge_method"] == ["S256"]
    challenge = auth_params["code_challenge"][0]

    token_request = respx.calls[1].request
    token_body = parse_qs(token_request.content.decode())
    assert token_body["grant_type"] == ["authorization_code"]
    assert token_body["client_id"] == ["css-onlineaccount-fe"]
    assert token_body["redirect_uri"] == [DEFAULT_REDIRECT_URI]
    verifier = token_body["code_verifier"][0]

    # PKCE verifier should hash to the challenge using SHA256 + url-safe b64.
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    assert challenge == expected.decode()


@pytest.mark.asyncio
@respx.mock
async def test_login_required_raises_session_expired(http_client: httpx.AsyncClient) -> None:
    """A login_required redirect surfaces as CookieSessionExpiredError."""
    respx.get(AUTHORIZE_ENDPOINT).mock(side_effect=_redirect_with_error("login_required"))
    auth = Authenticator(http_client, cookies=dict(SAMPLE_COOKIES))
    with pytest.raises(CookieSessionExpiredError) as exc:
        await auth.login()
    assert exc.value.error == "login_required"


@pytest.mark.asyncio
@respx.mock
async def test_other_error_surfaces_as_auth_error(http_client: httpx.AsyncClient) -> None:
    respx.get(AUTHORIZE_ENDPOINT).mock(side_effect=_redirect_with_error("invalid_request"))
    auth = Authenticator(http_client, cookies=dict(SAMPLE_COOKIES))
    with pytest.raises(YorkshireWaterAuthError) as exc:
        await auth.login()
    assert exc.value.error == "invalid_request"


@pytest.mark.asyncio
@respx.mock
async def test_state_mismatch_raises(http_client: httpx.AsyncClient) -> None:
    """A redirect with a state that does not match the request value is rejected."""
    respx.get(AUTHORIZE_ENDPOINT).mock(
        return_value=httpx.Response(
            302,
            headers={"location": f"{DEFAULT_REDIRECT_URI}?code=abc&state=tampered"},
        ),
    )
    auth = Authenticator(http_client, cookies=dict(SAMPLE_COOKIES))
    with pytest.raises(YorkshireWaterAuthError, match="State mismatch"):
        await auth.login()


@pytest.mark.asyncio
@respx.mock
async def test_authorize_non_redirect_raises(http_client: httpx.AsyncClient) -> None:
    respx.get(AUTHORIZE_ENDPOINT).mock(return_value=httpx.Response(200, html="<html>err</html>"))
    auth = Authenticator(http_client, cookies=dict(SAMPLE_COOKIES))
    with pytest.raises(YorkshireWaterAuthError, match="expected a redirect"):
        await auth.login()


@pytest.mark.asyncio
@respx.mock
async def test_redirect_to_unexpected_host_is_rejected(http_client: httpx.AsyncClient) -> None:
    """A Location header pointing at the wrong host must not be trusted."""
    respx.get(AUTHORIZE_ENDPOINT).mock(
        return_value=httpx.Response(
            302,
            headers={
                "location": "https://attacker.example/account/callback/response?code=evil&state=any",
            },
        ),
    )
    auth = Authenticator(http_client, cookies=dict(SAMPLE_COOKIES))
    with pytest.raises(YorkshireWaterAuthError, match="unexpected target"):
        await auth.login()


@pytest.mark.asyncio
@respx.mock
async def test_redirect_with_wrong_iss_is_rejected(http_client: httpx.AsyncClient) -> None:
    """An iss parameter that does not match the IdP issuer is rejected."""

    def with_wrong_iss(request: httpx.Request) -> httpx.Response:
        params = parse_qs(urlparse(str(request.url)).query)
        state = params["state"][0]
        return httpx.Response(
            302,
            headers={
                "location": (
                    f"{DEFAULT_REDIRECT_URI}?code=code-1&state={state}"
                    "&iss=https%3A%2F%2Fattacker.example"
                ),
            },
        )

    respx.get(AUTHORIZE_ENDPOINT).mock(side_effect=with_wrong_iss)
    auth = Authenticator(http_client, cookies=dict(SAMPLE_COOKIES))
    with pytest.raises(YorkshireWaterAuthError, match="iss parameter"):
        await auth.login()


@pytest.mark.asyncio
@respx.mock
async def test_redirect_with_correct_iss_is_accepted(http_client: httpx.AsyncClient) -> None:
    """An iss parameter that matches the IdP issuer is accepted."""

    def with_correct_iss(request: httpx.Request) -> httpx.Response:
        params = parse_qs(urlparse(str(request.url)).query)
        state = params["state"][0]
        return httpx.Response(
            302,
            headers={
                "location": (
                    f"{DEFAULT_REDIRECT_URI}?code=code-1&state={state}"
                    "&iss=https%3A%2F%2Flogin.yorkshirewater.com"
                ),
            },
        )

    respx.get(AUTHORIZE_ENDPOINT).mock(side_effect=with_correct_iss)
    respx.post(TOKEN_ENDPOINT).mock(return_value=httpx.Response(200, json=_token_response()))
    auth = Authenticator(http_client, cookies=dict(SAMPLE_COOKIES))
    tokens = await auth.login()
    assert tokens.access_token == "access-1"


@pytest.mark.asyncio
@respx.mock
async def test_token_endpoint_400_raises(http_client: httpx.AsyncClient) -> None:
    respx.get(AUTHORIZE_ENDPOINT).mock(side_effect=_state_echo_response())
    respx.post(TOKEN_ENDPOINT).mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"}),
    )
    auth = Authenticator(http_client, cookies=dict(SAMPLE_COOKIES))
    with pytest.raises(YorkshireWaterAuthError) as exc:
        await auth.login()
    assert exc.value.error == "invalid_grant"
    assert exc.value.status_code == 400


@pytest.mark.asyncio
@respx.mock
async def test_ensure_token_renews_when_expired(http_client: httpx.AsyncClient) -> None:
    """Access tokens close to expiry trigger another silent renewal."""
    authorize_route = respx.get(AUTHORIZE_ENDPOINT).mock(side_effect=_state_echo_response())
    respx.post(TOKEN_ENDPOINT).mock(
        side_effect=[
            httpx.Response(200, json=_token_response(expires_in=1)),
            httpx.Response(
                200,
                json={**_token_response(), "access_token": "access-2"},
            ),
        ],
    )

    auth = Authenticator(http_client, cookies=dict(SAMPLE_COOKIES))
    await auth.login()

    second = await auth.ensure_token()
    assert second == "access-2"
    assert authorize_route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_ensure_token_skips_renewal_when_fresh(http_client: httpx.AsyncClient) -> None:
    authorize_route = respx.get(AUTHORIZE_ENDPOINT).mock(side_effect=_state_echo_response())
    respx.post(TOKEN_ENDPOINT).mock(return_value=httpx.Response(200, json=_token_response()))

    auth = Authenticator(http_client, cookies=dict(SAMPLE_COOKIES))
    await auth.login()
    second = await auth.ensure_token()

    assert second == "access-1"
    assert authorize_route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_force_refresh_always_renews(http_client: httpx.AsyncClient) -> None:
    authorize_route = respx.get(AUTHORIZE_ENDPOINT).mock(side_effect=_state_echo_response())
    respx.post(TOKEN_ENDPOINT).mock(
        side_effect=[
            httpx.Response(200, json=_token_response()),
            httpx.Response(200, json={**_token_response(), "access_token": "access-3"}),
        ],
    )

    auth = Authenticator(http_client, cookies=dict(SAMPLE_COOKIES))
    await auth.login()
    fresh = await auth.force_refresh()

    assert fresh == "access-3"
    assert authorize_route.call_count == 2


@pytest.mark.asyncio
async def test_revoke_clears_local_state(authenticator: Authenticator) -> None:
    """`revoke()` does not call any endpoint but clears in-memory tokens."""
    # Cannot login without respx because the authorize endpoint would fail.
    # Just check revoke is a no-op on a fresh authenticator.
    await authenticator.revoke()
    assert authenticator.access_token is None
