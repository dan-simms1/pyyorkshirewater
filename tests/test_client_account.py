"""Client tests for the account endpoints and the HTTP error paths."""

from __future__ import annotations

import httpx
import pytest
import respx

from pyyorkshirewater.client import YorkshireWaterClient, _first_dict, _safe_text
from pyyorkshirewater.const import (
    API_BASE_URL,
    ENDPOINT_CURRENT_CONSUMPTION,
    ENDPOINT_CUSTOMER_DETAIL,
    ENDPOINT_DAILY_CONSUMPTION,
    ENDPOINT_METER_DETAILS,
    ENDPOINT_PROPERTIES,
    ENDPOINT_PROPERTIES_DETAIL,
    ENDPOINT_YEARLY_CONSUMPTION,
    ENDPOINT_YOUR_USAGE,
)
from pyyorkshirewater.exceptions import (
    YorkshireWaterAPIError,
    YorkshireWaterMeterNotReadyError,
    YorkshireWaterRateLimitError,
)
from pyyorkshirewater.models import MeterStatus

from .conftest import SAMPLE_COOKIES
from .test_client import (
    _consumption_payload,
    _meter_payload,
    _wire_silent_renewal_routes,
)

_ADDRESS = {
    "houseName": "",
    "houseNumber": "1",
    "addressLine1": "Example Street",
    "addressLine2": "Sometown",
    "addressLine3": "Anywhere",
    "addressLine4": None,
    "addressLine5": None,
    "postcode": "EX1 1EX",
}


def _property_payload(n: int) -> dict[str, object]:
    return {
        "accountReference": f"opaque-ref-{n}",
        "displayAccountReference": f"123456789012345{n}",
        "accountStatus": "Live",
        "address": _ADDRESS,
    }


def _url(path: str) -> str:
    return f"{API_BASE_URL}{path}"


async def _live_client(client: YorkshireWaterClient) -> YorkshireWaterClient:
    """Log in against mocked routes with a live meter."""
    _wire_silent_renewal_routes()
    respx.get(_url(ENDPOINT_METER_DETAILS)).mock(
        return_value=httpx.Response(200, json=_meter_payload(with_meter=True)),
    )
    respx.get(_url(ENDPOINT_CURRENT_CONSUMPTION)).mock(
        return_value=httpx.Response(200, json=_consumption_payload(live=True)),
    )
    await client.login()
    return client


# --- account endpoints -------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_get_customer(client: YorkshireWaterClient) -> None:
    _wire_silent_renewal_routes()
    respx.get(_url(ENDPOINT_CUSTOMER_DETAIL)).mock(
        return_value=httpx.Response(
            200,
            json={
                "title": "Mx",
                "forename": "Test",
                "surname": "User",
                "email": "user@example.com",
                "mobileTelephone": "07700 900000 ",
            },
        ),
    )

    customer = await client.get_customer()

    assert customer.full_name == "Mx Test User"
    assert customer.mobile_telephone == "07700 900000"


@pytest.mark.asyncio
@respx.mock
async def test_get_customer_non_dict_payload(client: YorkshireWaterClient) -> None:
    _wire_silent_renewal_routes()
    respx.get(_url(ENDPOINT_CUSTOMER_DETAIL)).mock(
        return_value=httpx.Response(200, json=[]),
    )

    customer = await client.get_customer()

    assert customer.full_name == ""


@pytest.mark.asyncio
@respx.mock
async def test_iter_properties_walks_pages(client: YorkshireWaterClient) -> None:
    _wire_silent_renewal_routes()
    pages = {
        "1": {"properties": [_property_payload(1)], "totalProperties": 2,
              "totalPages": 2, "pageNumber": 1},
        "2": {"properties": [_property_payload(2)], "totalProperties": 2,
              "totalPages": 2, "pageNumber": 2},
    }
    route = respx.get(_url(ENDPOINT_PROPERTIES)).mock(
        side_effect=lambda request: httpx.Response(
            200, json=pages[request.url.params["page"]],
        ),
    )

    properties = await client.iter_properties()

    assert [p.account_reference for p in properties] == ["opaque-ref-1", "opaque-ref-2"]
    assert route.call_count == 2
    assert properties[0].is_live is True
    assert properties[0].address.formatted() == (
        "1, Example Street, Sometown, Anywhere, EX1 1EX"
    )


@pytest.mark.asyncio
@respx.mock
async def test_list_properties_non_dict_payload(client: YorkshireWaterClient) -> None:
    _wire_silent_renewal_routes()
    respx.get(_url(ENDPOINT_PROPERTIES)).mock(return_value=httpx.Response(200, json=[]))

    page = await client.list_properties()

    assert page.properties == []
    assert page.total_pages == 0
    assert await client.iter_properties() == []


@pytest.mark.asyncio
@respx.mock
async def test_get_property_detail_returns_raw(client: YorkshireWaterClient) -> None:
    _wire_silent_renewal_routes()
    route = respx.get(_url(ENDPOINT_PROPERTIES_DETAIL)).mock(
        return_value=httpx.Response(200, json={"isMetered": True}),
    )

    detail = await client.get_property_detail("opaque-ref-1")

    assert detail == {"isMetered": True}
    assert route.calls.last.request.url.params["accountReference"] == "opaque-ref-1"


# --- smart-meter edge cases --------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_meter_details_404_is_empty(client: YorkshireWaterClient) -> None:
    _wire_silent_renewal_routes()
    respx.get(_url(ENDPOINT_METER_DETAILS)).mock(return_value=httpx.Response(404))

    scoped = await client.get_meter_details(account_reference="123456789012345")
    unscoped = await client.get_meter_details()

    assert scoped.meter_reference is None
    assert unscoped.meter_reference is None
    assert client.meter_status is MeterStatus.NO_METER


@pytest.mark.asyncio
@respx.mock
async def test_meter_details_other_errors_raise(client: YorkshireWaterClient) -> None:
    _wire_silent_renewal_routes()
    respx.get(_url(ENDPOINT_METER_DETAILS)).mock(
        return_value=httpx.Response(400, json="Invalid Account Reference."),
    )

    with pytest.raises(YorkshireWaterAPIError) as err:
        await client.get_meter_details()

    assert err.value.status_code == 400


@pytest.mark.asyncio
@respx.mock
async def test_login_survives_failing_probes(client: YorkshireWaterClient) -> None:
    """YW now rejects the unscoped probe with 400. login() must not raise."""
    _wire_silent_renewal_routes()
    respx.get(_url(ENDPOINT_METER_DETAILS)).mock(return_value=httpx.Response(400))

    await client.login()

    assert client.meter_status is MeterStatus.NO_METER


@pytest.mark.asyncio
@respx.mock
async def test_current_consumption_without_cached_meter_skips_call(
    client: YorkshireWaterClient,
) -> None:
    _wire_silent_renewal_routes()
    route = respx.get(_url(ENDPOINT_CURRENT_CONSUMPTION))

    consumption = await client.get_current_consumption()

    assert consumption.is_meter_bau is False
    assert route.call_count == 0


@pytest.mark.asyncio
@respx.mock
async def test_current_consumption_404_is_empty(client: YorkshireWaterClient) -> None:
    _wire_silent_renewal_routes()
    respx.get(_url(ENDPOINT_CURRENT_CONSUMPTION)).mock(return_value=httpx.Response(404))

    consumption = await client.get_current_consumption(meter_reference="WAKE-001")

    assert consumption.is_meter_bau is False


@pytest.mark.asyncio
@respx.mock
async def test_current_consumption_other_errors_raise(
    client: YorkshireWaterClient,
) -> None:
    _wire_silent_renewal_routes()
    respx.get(_url(ENDPOINT_CURRENT_CONSUMPTION)).mock(return_value=httpx.Response(500))

    with pytest.raises(YorkshireWaterAPIError):
        await client.get_current_consumption(meter_reference="WAKE-001")


@pytest.mark.asyncio
@respx.mock
async def test_your_usage_unwraps_envelope(client: YorkshireWaterClient) -> None:
    await _live_client(client)
    respx.get(_url(ENDPOINT_YOUR_USAGE)).mock(
        return_value=httpx.Response(
            200, json={"items": [{"month": "10", "totalConsumptionLitres": "3584"}]},
        ),
    )

    periods = await client.get_your_usage()

    assert [p.total_consumption_litres for p in periods] == [3584]


@pytest.mark.asyncio
@respx.mock
async def test_your_usage_unexpected_shape_is_empty(client: YorkshireWaterClient) -> None:
    await _live_client(client)
    respx.get(_url(ENDPOINT_YOUR_USAGE)).mock(return_value=httpx.Response(200, json="x"))

    assert await client.get_your_usage() == []


@pytest.mark.asyncio
@respx.mock
async def test_daily_consumption_defaults_dates_from_meter(
    client: YorkshireWaterClient,
) -> None:
    await _live_client(client)
    route = respx.get(_url(ENDPOINT_DAILY_CONSUMPTION)).mock(
        return_value=httpx.Response(
            200, json=[{"date": "2026-10-09", "totalConsumptionLitres": "747"}],
        ),
    )

    points = await client.get_daily_consumption(
        start_date="2026-10-01", end_date="2026-10-09",
    )

    assert [p.total_consumption_litres for p in points] == [747]
    params = route.calls.last.request.url.params
    assert params["moveInDate"] == "2026-04-01"
    assert params["moveOutDate"]


@pytest.mark.asyncio
@respx.mock
async def test_daily_consumption_needs_move_in_date(client: YorkshireWaterClient) -> None:
    """With no cached start date and none passed, fail before calling YW."""
    _wire_silent_renewal_routes()
    payload = _meter_payload(with_meter=True)
    payload["startDate"] = None
    respx.get(_url(ENDPOINT_METER_DETAILS)).mock(
        return_value=httpx.Response(200, json=payload),
    )
    respx.get(_url(ENDPOINT_CURRENT_CONSUMPTION)).mock(
        return_value=httpx.Response(200, json=_consumption_payload(live=True)),
    )
    await client.login()

    with pytest.raises(YorkshireWaterMeterNotReadyError):
        await client.get_daily_consumption(start_date="2026-10-01", end_date="2026-10-09")


@pytest.mark.asyncio
@respx.mock
async def test_daily_consumption_bad_list_is_empty(client: YorkshireWaterClient) -> None:
    await _live_client(client)
    respx.get(_url(ENDPOINT_DAILY_CONSUMPTION)).mock(
        return_value=httpx.Response(200, json={"dailyUsageData": "nope"}),
    )

    points = await client.get_daily_consumption(
        start_date="2026-10-01", end_date="2026-10-09",
    )

    assert points == []


@pytest.mark.asyncio
async def test_explicit_meter_reference_needed_without_cache(
    client: YorkshireWaterClient,
) -> None:
    with pytest.raises(YorkshireWaterMeterNotReadyError):
        client._resolve_meter_reference(None)
    assert client._resolve_meter_reference("WAKE-002") == "WAKE-002"


# --- HTTP error paths --------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_rate_limit_carries_retry_after(client: YorkshireWaterClient) -> None:
    _wire_silent_renewal_routes()
    respx.get(_url(ENDPOINT_CUSTOMER_DETAIL)).mock(
        return_value=httpx.Response(429, headers={"Retry-After": "30"}),
    )

    with pytest.raises(YorkshireWaterRateLimitError) as err:
        await client.get_customer()

    assert err.value.retry_after == 30.0


@pytest.mark.asyncio
@respx.mock
async def test_rate_limit_with_bad_retry_after(client: YorkshireWaterClient) -> None:
    _wire_silent_renewal_routes()
    respx.get(_url(ENDPOINT_CUSTOMER_DETAIL)).mock(
        return_value=httpx.Response(429, headers={"Retry-After": "soon"}),
    )

    with pytest.raises(YorkshireWaterRateLimitError) as err:
        await client.get_customer()

    assert err.value.retry_after is None


@pytest.mark.asyncio
@respx.mock
async def test_network_error_is_wrapped(client: YorkshireWaterClient) -> None:
    _wire_silent_renewal_routes()
    respx.get(_url(ENDPOINT_CUSTOMER_DETAIL)).mock(
        side_effect=httpx.ConnectError("unreachable"),
    )

    with pytest.raises(YorkshireWaterAPIError, match="Network error"):
        await client.get_customer()


@pytest.mark.asyncio
@respx.mock
async def test_non_json_body_raises_without_leaking_body(
    client: YorkshireWaterClient,
) -> None:
    _wire_silent_renewal_routes()
    respx.get(_url(ENDPOINT_CUSTOMER_DETAIL)).mock(
        return_value=httpx.Response(200, text="<html>fake-session-marker</html>"),
    )

    with pytest.raises(YorkshireWaterAPIError) as err:
        await client.get_customer()

    assert "fake-session-marker" not in str(err.value)
    assert "fake-session-marker" in (err.value.body or "")


@pytest.mark.asyncio
@respx.mock
async def test_empty_body_returns_none(client: YorkshireWaterClient) -> None:
    _wire_silent_renewal_routes()
    respx.get(_url(ENDPOINT_PROPERTIES_DETAIL)).mock(return_value=httpx.Response(204))

    assert await client.get_property_detail("opaque-ref-1") is None


# --- lifecycle and helpers ---------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_refresh_token_runs_renewal_only(client: YorkshireWaterClient) -> None:
    _wire_silent_renewal_routes()
    meter_route = respx.get(_url(ENDPOINT_METER_DETAILS))

    await client.refresh_token()

    assert meter_route.call_count == 0
    assert client.cookies == SAMPLE_COOKIES


@pytest.mark.asyncio
async def test_context_manager_closes_owned_client() -> None:
    async with YorkshireWaterClient(cookies=dict(SAMPLE_COOKIES)) as yw:
        http = yw._http
    assert http.is_closed


@pytest.mark.asyncio
async def test_context_manager_leaves_shared_client_open(
    http_client: httpx.AsyncClient,
) -> None:
    async with YorkshireWaterClient(cookies=dict(SAMPLE_COOKIES), http_client=http_client):
        pass
    assert not http_client.is_closed


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"wrapper": {"a": 1}}, {"a": 1}),
        ({"a": 1, "b": 2}, {"a": 1, "b": 2}),
        ({"only": 5}, {"only": 5}),
        ([{"a": 1}, {"a": 2}], {"a": 1}),
        ([], {}),
        (None, {}),
    ],
)
def test_first_dict(payload: object, expected: dict[str, object]) -> None:
    assert _first_dict(payload) == expected


# --- explicit meter references and remaining branches -----------------------


@pytest.mark.asyncio
@respx.mock
async def test_login_survives_consumption_probe_error(client: YorkshireWaterClient) -> None:
    _wire_silent_renewal_routes()
    respx.get(_url(ENDPOINT_METER_DETAILS)).mock(
        return_value=httpx.Response(200, json=_meter_payload(with_meter=True)),
    )
    respx.get(_url(ENDPOINT_CURRENT_CONSUMPTION)).mock(return_value=httpx.Response(500))

    await client.login()

    assert client.meter_status is MeterStatus.PENDING_ACTIVATION


@pytest.mark.asyncio
@respx.mock
async def test_login_with_consumption_404_caches_empty(client: YorkshireWaterClient) -> None:
    _wire_silent_renewal_routes()
    respx.get(_url(ENDPOINT_METER_DETAILS)).mock(
        return_value=httpx.Response(200, json=_meter_payload(with_meter=True)),
    )
    respx.get(_url(ENDPOINT_CURRENT_CONSUMPTION)).mock(return_value=httpx.Response(404))

    await client.login()

    assert client.meter_status is MeterStatus.PENDING_ACTIVATION


@pytest.mark.asyncio
@respx.mock
async def test_scoped_calls_do_not_touch_the_cache(client: YorkshireWaterClient) -> None:
    """Explicitly scoped calls work without login() and leave the cache alone."""
    _wire_silent_renewal_routes()
    respx.get(_url(ENDPOINT_METER_DETAILS)).mock(
        return_value=httpx.Response(200, json=_meter_payload(with_meter=True)),
    )
    respx.get(_url(ENDPOINT_CURRENT_CONSUMPTION)).mock(
        return_value=httpx.Response(200, json=_consumption_payload(live=True)),
    )
    respx.get(_url(ENDPOINT_YOUR_USAGE)).mock(return_value=httpx.Response(200, json=[]))
    respx.get(_url(ENDPOINT_DAILY_CONSUMPTION)).mock(
        return_value=httpx.Response(200, json={"dailyUsageData": []}),
    )
    respx.get(_url(ENDPOINT_YEARLY_CONSUMPTION)).mock(
        return_value=httpx.Response(200, json={"year": 2026, "monthlyConsumption": []}),
    )

    details = await client.get_meter_details(account_reference="123456789012345")
    consumption = await client.get_current_consumption(meter_reference="WAKE-001")
    usage = await client.get_your_usage(meter_reference="WAKE-001")
    daily = await client.get_daily_consumption(
        start_date="2026-10-01", end_date="2026-10-09",
        move_in_date="2026-04-01", meter_reference="WAKE-001",
    )
    yearly = await client.get_yearly_consumption(year=2026, meter_reference="WAKE-001")

    assert details.meter_reference == "WAKE-001"
    assert consumption.is_meter_bau is True
    assert usage == []
    assert daily == []
    assert yearly is not None
    assert yearly.year == 2026
    assert client.meter_status is MeterStatus.NO_METER


@pytest.mark.asyncio
@respx.mock
async def test_your_usage_dict_without_list_is_empty(client: YorkshireWaterClient) -> None:
    await _live_client(client)
    respx.get(_url(ENDPOINT_YOUR_USAGE)).mock(
        return_value=httpx.Response(200, json={"total": 3, "label": "x"}),
    )

    assert await client.get_your_usage() == []


@pytest.mark.asyncio
@respx.mock
async def test_yearly_consumption_other_errors_raise(client: YorkshireWaterClient) -> None:
    _wire_silent_renewal_routes()
    respx.get(_url(ENDPOINT_YEARLY_CONSUMPTION)).mock(return_value=httpx.Response(500))

    with pytest.raises(YorkshireWaterAPIError):
        await client.get_yearly_consumption(year=2026, meter_reference="WAKE-001")


def test_safe_text_handles_undecodable_body() -> None:
    class _Undecodable:
        @property
        def text(self) -> str:
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    assert _safe_text(_Undecodable()) is None  # type: ignore[arg-type]
