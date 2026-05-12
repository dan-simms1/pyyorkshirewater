"""Shared test fixtures."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio

from pyyorkshirewater.auth import Authenticator
from pyyorkshirewater.client import YorkshireWaterClient
from pyyorkshirewater.models import TokenSet

FIXTURES = Path(__file__).parent / "fixtures"

SAMPLE_COOKIES: dict[str, str] = {
    "idsrv": "fake-idsrv-cookie-value",
    "idsrv.session": "fake-session-cookie-value",
}


def load_fixture(name: str) -> Any:
    """Load a JSON fixture by file name (without extension)."""
    return json.loads((FIXTURES / f"{name}.json").read_text())


def make_token_set(
    *,
    access_token: str = "access-test",
    expires_in: int = 3600,
) -> TokenSet:
    """Create a TokenSet for tests. The SPA client never gets a refresh token."""
    return TokenSet(
        access_token=access_token,
        refresh_token=None,
        token_type="Bearer",
        expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
        scope="openid user-names css-onlineaccount-api css-registration-api",
    )


@pytest_asyncio.fixture
async def http_client() -> AsyncIterator[httpx.AsyncClient]:
    """Yield a real (non-mocked) httpx client for tests that mount respx routes."""
    async with httpx.AsyncClient() as client:
        yield client


@pytest.fixture
def authenticator(http_client: httpx.AsyncClient) -> Authenticator:
    """An Authenticator pre-wired with sample IdP cookies."""
    return Authenticator(http_client, cookies=dict(SAMPLE_COOKIES))


@pytest_asyncio.fixture
async def client(http_client: httpx.AsyncClient) -> AsyncIterator[YorkshireWaterClient]:
    """A YorkshireWaterClient that shares the same httpx client respx is patching."""
    yw = YorkshireWaterClient(
        cookies=dict(SAMPLE_COOKIES),
        http_client=http_client,
    )
    try:
        yield yw
    finally:
        # The shared http_client is closed by its own fixture.
        pass
