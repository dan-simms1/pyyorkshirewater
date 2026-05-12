"""Unit tests for `pyyorkshirewater.client`."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from pyyorkshirewater.client import YorkshireWaterClient
from pyyorkshirewater.const import (
    API_BASE_URL,
    AUTHORIZE_ENDPOINT,
    DEFAULT_REDIRECT_URI,
    ENDPOINT_CURRENT_CONSUMPTION,
    ENDPOINT_DAILY_CONSUMPTION,
    ENDPOINT_METER_DETAILS,
    ENDPOINT_YEARLY_CONSUMPTION,
    ENDPOINT_YOUR_USAGE,
    TOKEN_ENDPOINT,
)
from pyyorkshirewater.exceptions import (
    YorkshireWaterAPIError,
    YorkshireWaterMeterNotReadyError,
    YorkshireWaterRateLimitError,
)
from pyyorkshirewater.models import MeterStatus

from .conftest import SAMPLE_COOKIES


def _redirect_with_code() -> httpx.Response:
    """Return a 302 with a deterministic code, ignoring state."""
    return httpx.Response(
        302,
        headers={"location": f"{DEFAULT_REDIRECT_URI}?code=code-1"},
    )


def _state_aware_authorize_response(request: httpx.Request) -> httpx.Response:
    """Echo back the state so the silent renewal validates."""
    params = parse_qs(urlparse(str(request.url)).query)
    state = params.get("state", [""])[0]
    return httpx.Response(
        302,
        headers={"location": f"{DEFAULT_REDIRECT_URI}?code=code-1&state={state}"},
    )


def _token_response() -> dict[str, object]:
    return {
        "access_token": "access-1",
        "expires_in": 3600,
        "token_type": "Bearer",
        "scope": "openid user-names css-onlineaccount-api css-registration-api",
    }


def _meter_payload(*, with_meter: bool = True) -> dict[str, object]:
    if not with_meter:
        return {"meterReference": "", "startDate": None, "endDate": None, "currentDate": None}
    return {
        "meterReference": "WAKE-001",
        "startDate": "2026-04-01",
        "endDate": "2027-04-01",
        "currentDate": "2026-05-06",
    }


def _consumption_payload(*, live: bool = True) -> dict[str, object]:
    return {
        "isMeterBau": live,
        "currentContinuousFlowAlarmState": False,
        "currentContinuousFlowAlarmDetails": [],
    }


def _wire_silent_renewal_routes() -> None:
    respx.get(AUTHORIZE_ENDPOINT).mock(side_effect=_state_aware_authorize_response)
    respx.post(TOKEN_ENDPOINT).mock(return_value=httpx.Response(200, json=_token_response()))


@pytest.mark.asyncio
@respx.mock
async def test_login_populates_meter_status_live(client: YorkshireWaterClient) -> None:
    _wire_silent_renewal_routes()
    respx.get(f"{API_BASE_URL}{ENDPOINT_METER_DETAILS}").mock(
        return_value=httpx.Response(200, json=_meter_payload(with_meter=True)),
    )
    respx.get(f"{API_BASE_URL}{ENDPOINT_CURRENT_CONSUMPTION}").mock(
        return_value=httpx.Response(200, json=_consumption_payload(live=True)),
    )

    await client.login()
    assert client.meter_status is MeterStatus.LIVE


@pytest.mark.asyncio
@respx.mock
async def test_login_with_no_meter(client: YorkshireWaterClient) -> None:
    _wire_silent_renewal_routes()
    respx.get(f"{API_BASE_URL}{ENDPOINT_METER_DETAILS}").mock(
        return_value=httpx.Response(200, json=_meter_payload(with_meter=False)),
    )
    respx.get(f"{API_BASE_URL}{ENDPOINT_CURRENT_CONSUMPTION}").mock(
        return_value=httpx.Response(200, json=_consumption_payload(live=False)),
    )

    await client.login()
    assert client.meter_status is MeterStatus.NO_METER


@pytest.mark.asyncio
@respx.mock
async def test_login_with_pending_meter(client: YorkshireWaterClient) -> None:
    _wire_silent_renewal_routes()
    respx.get(f"{API_BASE_URL}{ENDPOINT_METER_DETAILS}").mock(
        return_value=httpx.Response(200, json=_meter_payload(with_meter=True)),
    )
    respx.get(f"{API_BASE_URL}{ENDPOINT_CURRENT_CONSUMPTION}").mock(
        return_value=httpx.Response(200, json=_consumption_payload(live=False)),
    )

    await client.login()
    assert client.meter_status is MeterStatus.PENDING_ACTIVATION


@pytest.mark.asyncio
@respx.mock
async def test_consumption_endpoints_require_live_meter(client: YorkshireWaterClient) -> None:
    _wire_silent_renewal_routes()
    respx.get(f"{API_BASE_URL}{ENDPOINT_METER_DETAILS}").mock(
        return_value=httpx.Response(200, json=_meter_payload(with_meter=True)),
    )
    respx.get(f"{API_BASE_URL}{ENDPOINT_CURRENT_CONSUMPTION}").mock(
        return_value=httpx.Response(200, json=_consumption_payload(live=False)),
    )

    await client.login()
    with pytest.raises(YorkshireWaterMeterNotReadyError):
        await client.get_your_usage()
    with pytest.raises(YorkshireWaterMeterNotReadyError):
        await client.get_daily_consumption()
    with pytest.raises(YorkshireWaterMeterNotReadyError):
        await client.get_yearly_consumption()


@pytest.mark.asyncio
@respx.mock
async def test_get_daily_consumption_passes_query_params(client: YorkshireWaterClient) -> None:
    _wire_silent_renewal_routes()
    respx.get(f"{API_BASE_URL}{ENDPOINT_METER_DETAILS}").mock(
        return_value=httpx.Response(200, json=_meter_payload(with_meter=True)),
    )
    respx.get(f"{API_BASE_URL}{ENDPOINT_CURRENT_CONSUMPTION}").mock(
        return_value=httpx.Response(200, json=_consumption_payload(live=True)),
    )
    daily_route = respx.get(f"{API_BASE_URL}{ENDPOINT_DAILY_CONSUMPTION}").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"date": "2026-05-05", "consumption": 123.4},
                {"date": "2026-05-06", "consumption": 98.0},
            ],
        ),
    )

    await client.login()
    points = await client.get_daily_consumption(
        start_date="2026-05-01",
        end_date="2026-05-06",
        unit="m3",
    )

    assert len(points) == 2
    request_url = daily_route.calls.last.request.url
    assert request_url.params["startDate"] == "2026-05-01"
    assert request_url.params["endDate"] == "2026-05-06"
    assert request_url.params["unit"] == "m3"


@pytest.mark.asyncio
@respx.mock
async def test_yearly_consumption_unwraps_envelope(client: YorkshireWaterClient) -> None:
    _wire_silent_renewal_routes()
    respx.get(f"{API_BASE_URL}{ENDPOINT_METER_DETAILS}").mock(
        return_value=httpx.Response(200, json=_meter_payload(with_meter=True)),
    )
    respx.get(f"{API_BASE_URL}{ENDPOINT_CURRENT_CONSUMPTION}").mock(
        return_value=httpx.Response(200, json=_consumption_payload(live=True)),
    )
    respx.get(f"{API_BASE_URL}{ENDPOINT_YEARLY_CONSUMPTION}").mock(
        return_value=httpx.Response(
            200,
            json={"data": [{"year": 2025, "consumption": 100000}]},
        ),
    )

    await client.login()
    points = await client.get_yearly_consumption()

    assert len(points) == 1
    assert points[0].year == 2025


@pytest.mark.asyncio
@respx.mock
async def test_your_usage_returns_periods(client: YorkshireWaterClient) -> None:
    _wire_silent_renewal_routes()
    respx.get(f"{API_BASE_URL}{ENDPOINT_METER_DETAILS}").mock(
        return_value=httpx.Response(200, json=_meter_payload(with_meter=True)),
    )
    respx.get(f"{API_BASE_URL}{ENDPOINT_CURRENT_CONSUMPTION}").mock(
        return_value=httpx.Response(200, json=_consumption_payload(live=True)),
    )
    respx.get(f"{API_BASE_URL}{ENDPOINT_YOUR_USAGE}").mock(
        return_value=httpx.Response(200, json=[{"a": 1}, {"a": 2}, {"a": 3}]),
    )

    await client.login()
    periods = await client.get_your_usage()

    assert len(periods) == 3


@pytest.mark.asyncio
@respx.mock
async def test_401_triggers_renewal_and_retries(client: YorkshireWaterClient) -> None:
    """A 401 forces another silent renewal then retries the original call."""
    authorize_route = respx.get(AUTHORIZE_ENDPOINT).mock(
        side_effect=_state_aware_authorize_response,
    )
    token_route = respx.post(TOKEN_ENDPOINT).mock(
        side_effect=[
            httpx.Response(200, json=_token_response()),
            httpx.Response(200, json={**_token_response(), "access_token": "access-2"}),
        ],
    )
    meter_route = respx.get(f"{API_BASE_URL}{ENDPOINT_METER_DETAILS}").mock(
        side_effect=[
            httpx.Response(401),
            httpx.Response(200, json=_meter_payload(with_meter=True)),
        ],
    )
    respx.get(f"{API_BASE_URL}{ENDPOINT_CURRENT_CONSUMPTION}").mock(
        return_value=httpx.Response(200, json=_consumption_payload(live=True)),
    )

    await client.login()

    assert authorize_route.call_count == 2
    assert token_route.call_count == 2
    assert meter_route.call_count == 2
    assert client.meter_status is MeterStatus.LIVE


@pytest.mark.asyncio
@respx.mock
async def test_429_raises_rate_limit_error(client: YorkshireWaterClient) -> None:
    _wire_silent_renewal_routes()
    respx.get(f"{API_BASE_URL}{ENDPOINT_METER_DETAILS}").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "12"}),
    )

    with pytest.raises(YorkshireWaterRateLimitError) as exc:
        await client.get_meter_details()
    assert exc.value.retry_after == pytest.approx(12.0)


@pytest.mark.asyncio
@respx.mock
async def test_5xx_raises_api_error(client: YorkshireWaterClient) -> None:
    _wire_silent_renewal_routes()
    respx.get(f"{API_BASE_URL}{ENDPOINT_METER_DETAILS}").mock(
        return_value=httpx.Response(503, text="upstream down"),
    )

    with pytest.raises(YorkshireWaterAPIError) as exc:
        await client.get_meter_details()
    assert exc.value.status_code == 503
    assert exc.value.body is not None
    assert "upstream down" in exc.value.body


@pytest.mark.asyncio
@respx.mock
async def test_meter_details_404_returns_empty_no_meter(client: YorkshireWaterClient) -> None:
    """An account with no meter yet sees /smartmeter/meter-details return 404."""
    _wire_silent_renewal_routes()
    respx.get(f"{API_BASE_URL}{ENDPOINT_METER_DETAILS}").mock(
        return_value=httpx.Response(404, json={"error": "not found"}),
    )
    respx.get(f"{API_BASE_URL}{ENDPOINT_CURRENT_CONSUMPTION}").mock(
        return_value=httpx.Response(404, json={"error": "not found"}),
    )

    await client.login()
    assert client.meter_status is MeterStatus.NO_METER

    # Direct calls also return empty objects rather than raising.
    details = await client.get_meter_details()
    assert details.meter_reference is None
    consumption = await client.get_current_consumption()
    assert consumption.is_meter_bau is False


@pytest.mark.asyncio
@respx.mock
async def test_meter_details_500_still_raises(client: YorkshireWaterClient) -> None:
    """A 5xx on the readiness probe still raises (not the 'no meter' path)."""
    _wire_silent_renewal_routes()
    respx.get(f"{API_BASE_URL}{ENDPOINT_METER_DETAILS}").mock(
        return_value=httpx.Response(503, text="upstream"),
    )
    with pytest.raises(YorkshireWaterAPIError) as exc:
        await client.get_meter_details()
    assert exc.value.status_code == 503


@pytest.mark.asyncio
@respx.mock
async def test_close_does_not_revoke_anything() -> None:
    """`close()` releases the HTTP client but does not invalidate the session."""
    _wire_silent_renewal_routes()
    respx.get(f"{API_BASE_URL}{ENDPOINT_METER_DETAILS}").mock(
        return_value=httpx.Response(200, json=_meter_payload(with_meter=True)),
    )
    respx.get(f"{API_BASE_URL}{ENDPOINT_CURRENT_CONSUMPTION}").mock(
        return_value=httpx.Response(200, json=_consumption_payload(live=True)),
    )

    async with YorkshireWaterClient(cookies=dict(SAMPLE_COOKIES)) as yw:
        await yw.login()

    # No revocation endpoint should have been hit; the only POST in the trace
    # is /connect/token from the silent renewal.
    revoke_calls = [c for c in respx.calls if "/connect/revocation" in str(c.request.url)]
    assert revoke_calls == []
