"""High-level async client for the Yorkshire Water customer self-service API.

Authentication is cookie-based silent renewal against the IdentityServer at
`login.yorkshirewater.com`. The user logs in once via their own browser
(reCAPTCHA passes naturally there) and exports the IdP session cookies. The
library uses those cookies to mint access tokens via
`/connect/authorize?prompt=none` whenever the cached access token expires.

A typical use looks like:

    async with YorkshireWaterClient(cookies={"idsrv": "..."}) as client:
        await client.login()
        if client.meter_status is MeterStatus.LIVE:
            data = await client.get_daily_consumption()
"""

from __future__ import annotations

import logging
from datetime import date
from types import TracebackType
from typing import Any, Self

import httpx

from .auth import Authenticator
from .const import (
    API_BASE_URL,
    DEFAULT_CLIENT_ID,
    DEFAULT_REDIRECT_URI,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    DEFAULT_SCOPES,
    DEFAULT_USER_AGENT,
    ENDPOINT_CURRENT_CONSUMPTION,
    ENDPOINT_CUSTOMER_DETAIL,
    ENDPOINT_DAILY_CONSUMPTION,
    ENDPOINT_METER_DETAILS,
    ENDPOINT_PROPERTIES,
    ENDPOINT_PROPERTIES_DETAIL,
    ENDPOINT_YEARLY_CONSUMPTION,
    ENDPOINT_YOUR_USAGE,
    PACKAGE_NAME,
)
from .exceptions import (
    YorkshireWaterAPIError,
    YorkshireWaterMeterNotReadyError,
    YorkshireWaterRateLimitError,
)
from .models import (
    CurrentConsumption,
    Customer,
    DailyConsumptionPoint,
    MeterDetails,
    MeterStatus,
    PropertiesPage,
    Property,
    UsagePeriod,
    YearlyConsumption,
)

_LOGGER = logging.getLogger(PACKAGE_NAME)


class YorkshireWaterClient:
    """Async client for `my.yorkshirewater.com`.

    The client is authenticated by IdentityServer cookies harvested from the
    user's logged-in browser session. There is no email/password support
    because the SPA OAuth client does not allow ROPC.
    """

    def __init__(
        self,
        *,
        cookies: dict[str, str],
        client_id: str = DEFAULT_CLIENT_ID,
        scopes: str = DEFAULT_SCOPES,
        redirect_uri: str = DEFAULT_REDIRECT_URI,
        api_base_url: str = API_BASE_URL,
        http_client: httpx.AsyncClient | None = None,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        """Configure the client.

        Args:
            cookies: Mapping of cookie names to values harvested from a
                logged-in browser session at `login.yorkshirewater.com`.
                Required.
            client_id: OAuth client id. Defaults to the SPA value.
            scopes: Space-separated OAuth scopes. Must match the SPA's set.
            redirect_uri: Authorization callback URI registered for the SPA.
                The library never visits this URL, only parses the code from
                the redirect Location.
            api_base_url: Base URL of the customer API.
            http_client: Optional pre-configured httpx async client. When None
                a new one is created and is closed on exit.
            request_timeout: Default per-request timeout in seconds.
        """
        self._api_base_url = api_base_url.rstrip("/")
        self._request_timeout = request_timeout
        self._owns_http = http_client is None
        self._http = http_client or httpx.AsyncClient(
            timeout=request_timeout,
            headers={"User-Agent": DEFAULT_USER_AGENT},
        )
        self._auth = Authenticator(
            self._http,
            cookies=cookies,
            client_id=client_id,
            scopes=scopes,
            redirect_uri=redirect_uri,
        )
        self._meter_details: MeterDetails | None = None
        self._current_consumption: CurrentConsumption | None = None

    async def __aenter__(self) -> Self:
        """Enter the async context. Does not log in eagerly."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the underlying HTTP client if we own it.

        Tokens are deliberately NOT revoked here. The IdP session cookie is
        the long-lived credential, and the user is expected to manage it via
        their browser.
        """
        await self.close()

    async def close(self) -> None:
        """Release HTTP resources without invalidating the session."""
        if self._owns_http:
            await self._http.aclose()

    @property
    def cookies(self) -> dict[str, str]:
        """Return a copy of the IdP cookies the client is using."""
        return self._auth.cookies

    @property
    def meter_status(self) -> MeterStatus:
        """Return the current meter readiness state.

        Returns `NO_METER` until `get_meter_details()` has been called and
        re-evaluates as `PENDING_ACTIVATION` or `LIVE` once both meter details
        and current consumption have been fetched.
        """
        if not self._meter_details or not self._meter_details.meter_reference:
            return MeterStatus.NO_METER
        if self._current_consumption and self._current_consumption.is_meter_bau:
            return MeterStatus.LIVE
        return MeterStatus.PENDING_ACTIVATION

    async def login(self) -> None:
        """Acquire an access token and refresh the readiness probes."""
        await self._auth.login()
        try:
            self._meter_details = await self.get_meter_details()
        except YorkshireWaterAPIError as err:
            _LOGGER.debug("Meter details probe failed at login: %s", err)
            self._meter_details = None
        try:
            self._current_consumption = await self.get_current_consumption()
        except YorkshireWaterAPIError as err:
            _LOGGER.debug("Current consumption probe failed at login: %s", err)
            self._current_consumption = None

    async def get_meter_details(
        self,
        *,
        account_reference: str | None = None,
    ) -> MeterDetails:
        """GET /smartmeter/meter-details.

        Yorkshire Water returns 404 for accounts with no smart meter yet.
        That is a normal state during the rollout, not an error: we return
        an empty `MeterDetails` so callers can read `meter_status` to drive
        their UI rather than handling exceptions.

        For multi-property accounts, pass the long opaque
        `account_reference` (from `Property.account_reference`) to scope
        the call to a specific property.
        """
        params = _build_query({"accountReference": account_reference})
        try:
            payload = await self._get(ENDPOINT_METER_DETAILS, params=params)
        except YorkshireWaterAPIError as err:
            if err.status_code == 404:
                empty = MeterDetails.from_api({})
                if account_reference is None:
                    self._meter_details = empty
                return empty
            raise
        details = MeterDetails.from_api(_first_dict(payload))
        if account_reference is None:
            self._meter_details = details
        return details

    async def get_current_consumption(
        self,
        *,
        meter_reference: str | None = None,
    ) -> CurrentConsumption:
        """GET /smartmeter/current-consumption.

        The endpoint requires a `meterReference` query parameter. When
        called without one, defaults to the meter reference cached from
        the most recent `get_meter_details()` call (the one `login()`
        makes automatically). Pre-LIVE meters return 404 here, which we
        translate into an empty `CurrentConsumption` so callers can
        drive UI from `meter_status` rather than from exceptions.

        For multi-property accounts, fetch `get_meter_details(
        account_reference=...)` first to obtain that property's
        `meter_reference`, then pass it explicitly.
        """
        # Probe semantics: when there's no cached meter (e.g. login()
        # ran but meter-details returned 404), return an empty object
        # rather than raising. Callers consume `meter_status` to know
        # whether the meter is live.
        if meter_reference is None and (
            not self._meter_details or not self._meter_details.meter_reference
        ):
            empty = CurrentConsumption.from_api({})
            self._current_consumption = empty
            return empty

        resolved = self._resolve_meter_reference(meter_reference)
        params = {"meterReference": resolved}
        try:
            payload = await self._get(ENDPOINT_CURRENT_CONSUMPTION, params=params)
        except YorkshireWaterAPIError as err:
            if err.status_code == 404:
                empty = CurrentConsumption.from_api({})
                if meter_reference is None:
                    self._current_consumption = empty
                return empty
            raise
        consumption = CurrentConsumption.from_api(_first_dict(payload))
        if meter_reference is None:
            self._current_consumption = consumption
        return consumption

    async def get_your_usage(
        self,
        *,
        meter_reference: str | None = None,
    ) -> list[UsagePeriod]:
        """GET /smartmeter/your-usage.

        Defaults to the cached meter reference when called without one.
        See `get_current_consumption` for the multi-property pattern.
        """
        if meter_reference is None:
            self._require_live_meter()
        resolved = self._resolve_meter_reference(meter_reference)
        params = {"meterReference": resolved}
        payload = await self._get(ENDPOINT_YOUR_USAGE, params=params)
        if isinstance(payload, list):
            return [UsagePeriod.from_api(p) for p in payload if isinstance(p, dict)]
        if isinstance(payload, dict):
            for value in payload.values():
                if isinstance(value, list):
                    return [UsagePeriod.from_api(p) for p in value if isinstance(p, dict)]
        return []

    async def get_daily_consumption(
        self,
        *,
        start_date: str,
        end_date: str,
        move_in_date: str | None = None,
        move_out_date: str | None = None,
        time_period: int = 1,
        meter_reference: str | None = None,
    ) -> list[DailyConsumptionPoint]:
        """GET /smartmeter/daily-consumption.

        Verified empirically on 2026-06-13. Required query parameters
        are `meterReference`, `startDate` (YYYY-MM-DD), `endDate`
        (YYYY-MM-DD), `moveInDate` (the customer's move-in date,
        practically the meter's `start_date`), `moveOutDate` (today
        for active customers) and `timePeriod` (1 for daily). The
        response wrapper carries window totals plus a `dailyUsageData`
        list of per-day records; we expose just the per-day list.

        `move_in_date` defaults to the cached meter details' `start_date`
        (set by login()). `move_out_date` defaults to today (UTC).
        """
        if meter_reference is None:
            self._require_live_meter()
        resolved = self._resolve_meter_reference(meter_reference)

        if move_in_date is None:
            if self._meter_details and self._meter_details.start_date:
                move_in_date = self._meter_details.start_date.isoformat()
            else:
                raise YorkshireWaterMeterNotReadyError(
                    "move_in_date is required. Pass it explicitly or "
                    "call get_meter_details() first so it can be "
                    "derived from the cached meter details.",
                )
        if move_out_date is None:
            move_out_date = date.today().isoformat()

        params = {
            "meterReference": resolved,
            "startDate": start_date,
            "endDate": end_date,
            "moveInDate": move_in_date,
            "moveOutDate": move_out_date,
            "timePeriod": str(time_period),
        }
        payload = await self._get(ENDPOINT_DAILY_CONSUMPTION, params=params)
        if isinstance(payload, dict):
            days_raw = payload.get("dailyUsageData") or []
            if not isinstance(days_raw, list):
                days_raw = []
        else:
            days_raw = payload if isinstance(payload, list) else []
        return [
            DailyConsumptionPoint.from_api(p)
            for p in days_raw
            if isinstance(p, dict)
        ]

    async def get_yearly_consumption(
        self,
        *,
        year: int,
        meter_reference: str | None = None,
    ) -> YearlyConsumption | None:
        """GET /smartmeter/yearly-consumption.

        The endpoint requires both `meterReference` and `year` as query
        parameters. Returns a single `YearlyConsumption` summary for
        that year, including the monthly breakdown, year-to-date
        totals and per-month averages.

        Defaults to the cached meter reference when called without
        one. Returns None if the meter has no data for the requested
        year (the endpoint returns 404).
        """
        if meter_reference is None:
            self._require_live_meter()
        resolved = self._resolve_meter_reference(meter_reference)
        params = {
            "meterReference": resolved,
            "year": str(year),
        }
        try:
            payload = await self._get(ENDPOINT_YEARLY_CONSUMPTION, params=params)
        except YorkshireWaterAPIError as err:
            if err.status_code == 404:
                return None
            raise
        if not isinstance(payload, dict):
            return None
        return YearlyConsumption.from_api(payload)

    def _resolve_meter_reference(self, override: str | None) -> str:
        """Return the meterReference to use for a consumption-endpoint call.

        Returns `override` if supplied, otherwise the meter reference
        cached from the most recent `get_meter_details()` call. Raises
        `YorkshireWaterMeterNotReadyError` if neither source has a value,
        which is the same error type already used elsewhere when the
        meter is not yet commissioned.
        """
        if override is not None:
            return override
        if self._meter_details and self._meter_details.meter_reference:
            return self._meter_details.meter_reference
        raise YorkshireWaterMeterNotReadyError(
            "No meter reference is available. Call get_meter_details() "
            "first (or pass meter_reference= explicitly).",
        )

    async def get_customer(self) -> Customer:
        """GET /api/account/customer/detail.

        Returns the customer's name, email, and contact phone. Yorkshire
        Water do not expose a single customer ID at this endpoint;
        per-property account numbers come from `list_properties`.
        """
        payload = await self._get(ENDPOINT_CUSTOMER_DETAIL)
        if not isinstance(payload, dict):
            return Customer.from_api({})
        return Customer.from_api(payload)

    async def list_properties(
        self,
        *,
        page: int = 1,
        page_size: int = 10,
    ) -> PropertiesPage:
        """GET /api/account/properties.

        Returns one page of the customer's properties. For accounts
        with more than `page_size` properties, call repeatedly with
        increasing `page`, or use `iter_properties()` to get all in one
        async iteration.
        """
        params = _build_query({"page": str(page), "pageSize": str(page_size)})
        payload = await self._get(ENDPOINT_PROPERTIES, params=params)
        if not isinstance(payload, dict):
            return PropertiesPage.from_api({})
        return PropertiesPage.from_api(payload)

    async def iter_properties(
        self,
        *,
        page_size: int = 10,
    ) -> list[Property]:
        """Return every property on the account, walking pagination.

        The customer portal uses pageSize=10. We follow the same default
        but expose it for callers who want a different chunking.
        """
        all_properties: list[Property] = []
        page = 1
        while True:
            result = await self.list_properties(page=page, page_size=page_size)
            all_properties.extend(result.properties)
            if (
                not result.properties
                or result.total_pages == 0
                or page >= result.total_pages
            ):
                break
            page += 1
        return all_properties

    async def get_property_detail(self, account_reference: str) -> Any:
        """GET /api/account/properties/detail.

        Takes the long opaque `account_reference` from `Property.account_reference`.
        Returns the raw JSON. The shape is not yet modelled because
        the field set is broad and not yet needed by the integration.
        """
        params = _build_query({"accountReference": account_reference})
        return await self._get(ENDPOINT_PROPERTIES_DETAIL, params=params)

    def _require_live_meter(self) -> None:
        """Raise if the meter is not in the LIVE state."""
        status = self.meter_status
        if status is not MeterStatus.LIVE:
            raise YorkshireWaterMeterNotReadyError(
                f"Smart meter is not ready (status={status.value}). "
                "Call get_meter_details() and get_current_consumption() and confirm "
                "MeterStatus.LIVE before requesting consumption data.",
            )

    async def _get(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
    ) -> Any:
        """GET a path on the customer API and return the decoded JSON body."""
        url = f"{self._api_base_url}{path}"
        return await self._request("GET", url, params=params)

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        data: Any = None,
        json_body: Any = None,
        retry_on_unauthorised: bool = True,
    ) -> Any:
        access_token = await self._auth.ensure_token()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        try:
            response = await self._http.request(
                method,
                url,
                params=params,
                data=data,
                json=json_body,
                headers=headers,
                timeout=self._request_timeout,
            )
        except httpx.HTTPError as err:
            raise YorkshireWaterAPIError(
                f"Network error calling {method} {url}: {err}",
            ) from err

        if response.status_code == 401 and retry_on_unauthorised:
            _LOGGER.debug("Got 401 on %s, forcing token renewal and retrying once", url)
            # If force_refresh raises CookieSessionExpiredError it propagates
            # so the HA reauth flow can prompt the user for fresh cookies.
            await self._auth.force_refresh()
            return await self._request(
                method,
                url,
                params=params,
                data=data,
                json_body=json_body,
                retry_on_unauthorised=False,
            )

        if response.status_code == 429:
            retry_after_raw = response.headers.get("Retry-After")
            retry_after: float | None
            try:
                retry_after = float(retry_after_raw) if retry_after_raw else None
            except (TypeError, ValueError):
                retry_after = None
            raise YorkshireWaterRateLimitError(
                f"Rate limited by {method} {url}",
                retry_after=retry_after,
                body=_safe_text(response),
            )

        if response.status_code >= 400:
            raise YorkshireWaterAPIError(
                f"{method} {url} returned HTTP {response.status_code}",
                status_code=response.status_code,
                body=_safe_text(response),
            )

        if not response.content:
            return None

        try:
            return response.json()
        except ValueError as err:
            # The body could contain partially-rendered HTML with session
            # markers. Keep it on the exception object's `body` attribute
            # for debugging but never inline it into the error message,
            # which Home Assistant may surface in user-visible repairs.
            raise YorkshireWaterAPIError(
                f"{method} {url} returned a non-JSON body.",
                status_code=response.status_code,
                body=_safe_text(response),
            ) from err


def _safe_text(response: httpx.Response) -> str | None:
    """Return the response body text, truncated. Returns None on decode failure."""
    try:
        return response.text[:1024]
    except UnicodeDecodeError:
        return None


def _first_dict(payload: Any) -> dict[str, Any]:
    """Coerce a payload into a dict for `from_api` consumers.

    The portal sometimes wraps the meaningful object in a single-key envelope
    or a single-element list. We tolerate both shapes. If the outer dict has
    exactly one key whose value is a dict, we unwrap it.
    """
    if isinstance(payload, dict):
        if len(payload) == 1:
            inner = next(iter(payload.values()))
            if isinstance(inner, dict):
                return inner
        return payload
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        return payload[0]
    return {}


def _build_query(params: dict[str, str | None]) -> dict[str, str]:
    """Drop None values from a query parameter dictionary."""
    return {k: v for k, v in params.items() if v is not None}
