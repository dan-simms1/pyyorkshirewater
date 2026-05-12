"""Typed data models for Yorkshire Water API responses.

Plain dataclasses are used in preference to pydantic to keep the dependency
footprint small. Each model has a `from_api` classmethod that accepts the raw
JSON dictionary returned by the customer API.

Most field names within the smart meter responses are inferred from the
minified portal bundle. Where a field cannot be confirmed it is documented as
an ASSUMPTION and a defensive `.get` is used so a missing field does not
raise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any


class MeterStatus(StrEnum):
    """Three-state model for whether a smart meter is reporting data.

    Mirrors the logic baked into the customer portal: a meter is only useful
    when an account has a `meterReference` and `isMeterBau` is true.
    """

    NO_METER = "no_meter"
    PENDING_ACTIVATION = "pending_activation"
    LIVE = "live"


def _parse_date(value: str | None) -> date | None:
    """Parse an ISO-8601 date string. Returns None for missing or sentinel values."""
    if not value:
        return None
    # The portal uses a sentinel string in some date fields. We do not yet know
    # the exact format. Anything we cannot parse becomes None rather than raising.
    try:
        return date.fromisoformat(value[:10])
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: str | None) -> datetime | None:
    """Parse an ISO-8601 datetime string. Returns None on missing or invalid values."""
    if not value:
        return None
    try:
        # `datetime.fromisoformat` in Python 3.11 accepts trailing `Z`.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class MeterDetails:
    """Response shape of GET /smartmeter/meter-details."""

    meter_reference: str | None
    start_date: date | None
    end_date: date | None
    current_date: date | None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> MeterDetails:
        """Build a `MeterDetails` from the raw API payload."""
        return cls(
            meter_reference=payload.get("meterReference") or None,
            start_date=_parse_date(payload.get("startDate")),
            end_date=_parse_date(payload.get("endDate")),
            current_date=_parse_date(payload.get("currentDate")),
            raw=payload,
        )


@dataclass(slots=True)
class ContinuousFlowAlarm:
    """One entry in `currentContinuousFlowAlarmDetails`."""

    alarm_start: datetime | None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> ContinuousFlowAlarm:
        """Build a `ContinuousFlowAlarm` from the raw API payload."""
        return cls(
            alarm_start=_parse_datetime(payload.get("alarmStartDate")),
            raw=payload,
        )


@dataclass(slots=True)
class CurrentConsumption:
    """Response shape of GET /smartmeter/current-consumption."""

    is_meter_bau: bool
    continuous_flow_alarm_state: bool
    continuous_flow_alarm_details: list[ContinuousFlowAlarm]
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> CurrentConsumption:
        """Build a `CurrentConsumption` from the raw API payload."""
        details_raw = payload.get("currentContinuousFlowAlarmDetails") or []
        if isinstance(details_raw, dict):
            details_raw = [details_raw]
        elif not isinstance(details_raw, list):
            details_raw = []
        return cls(
            is_meter_bau=bool(payload.get("isMeterBau", False)),
            continuous_flow_alarm_state=bool(payload.get("currentContinuousFlowAlarmState", False)),
            continuous_flow_alarm_details=[
                ContinuousFlowAlarm.from_api(d) for d in details_raw if isinstance(d, dict)
            ],
            raw=payload,
        )


def _coerce_float(value: Any) -> float | None:
    """Best-effort coercion to float. Returns None on failure or empty string."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> int | None:
    """Best-effort coercion to int. Returns None on failure or empty string."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_bool(value: Any) -> bool:
    """Coerce truthy JSON values to bool, defaulting to False."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y"}
    return bool(value)


@dataclass(slots=True)
class DailyConsumptionPoint:
    """One day in the `daily-consumption` time series.

    Field names mirror the camelCase keys the SPA's chart code reads from
    the API response (per static analysis of the customer portal bundle):
    `totalConsumptionLitres`, `totalConsumption` (m³ per the SPA's YEAR
    view comparison), `totalCost`, `totalCostIncludingSewerage`,
    `cleanWaterCost`, `isEstimatedConsumption`, `continuousFlowAlarm`.

    All numeric fields are optional and may be None if the API omits them
    or returns an empty string.
    """

    point_date: date | None
    total_consumption_litres: float | None
    total_consumption_m3: float | None
    total_cost: float | None
    total_cost_including_sewerage: float | None
    clean_water_cost: float | None
    is_estimated: bool
    continuous_flow_alarm: bool
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> DailyConsumptionPoint:
        """Build a `DailyConsumptionPoint` from the raw API payload."""
        date_raw = payload.get("date") or payload.get("readingDate")
        return cls(
            point_date=_parse_date(date_raw),
            total_consumption_litres=_coerce_float(payload.get("totalConsumptionLitres")),
            total_consumption_m3=_coerce_float(payload.get("totalConsumption")),
            total_cost=_coerce_float(payload.get("totalCost")),
            total_cost_including_sewerage=_coerce_float(
                payload.get("totalCostIncludingSewerage")
                or payload.get("totalCostInclSewerage")
                or payload.get("totalCostIncSewerage"),
            ),
            clean_water_cost=_coerce_float(payload.get("cleanWaterCost")),
            is_estimated=_coerce_bool(payload.get("isEstimatedConsumption")),
            continuous_flow_alarm=_coerce_bool(payload.get("continuousFlowAlarm")),
            raw=payload,
        )


@dataclass(slots=True)
class UsagePeriod:
    """One entry in the `your-usage` array.

    Each period (current, previous, prior) carries period-level totals,
    daily averages and the per-day breakdown. Field names mirror the
    SPA's expected response shape per static bundle analysis.
    """

    period_total_litres: float | None
    period_total_consumption_m3: float | None
    period_total_cost: float | None
    period_total_cost_including_sewerage: float | None
    period_total_clean_water_cost: float | None
    period_total_sewerage_cost: float | None
    daily_litres_average: float | None
    daily_cost_average: float | None
    daily_points: list[DailyConsumptionPoint]
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> UsagePeriod:
        """Build a `UsagePeriod` from the raw API payload."""
        days_raw = payload.get("dailyValues") or payload.get("days") or []
        if not isinstance(days_raw, list):
            days_raw = []
        return cls(
            period_total_litres=_coerce_float(payload.get("totalLitres")),
            period_total_consumption_m3=_coerce_float(payload.get("totalConsumption")),
            period_total_cost=_coerce_float(payload.get("totalCost")),
            period_total_cost_including_sewerage=_coerce_float(
                payload.get("totalCostIncludingSewerage"),
            ),
            period_total_clean_water_cost=_coerce_float(
                payload.get("totalStandardTariffCleanWaterCost")
                or payload.get("totalCleanWaterCost"),
            ),
            period_total_sewerage_cost=_coerce_float(
                payload.get("totalStandardTariffSewerageCost")
                or payload.get("totalSewerageCost"),
            ),
            daily_litres_average=_coerce_float(payload.get("dailyLitresAverage")),
            daily_cost_average=_coerce_float(payload.get("dailyCostAverage")),
            daily_points=[
                DailyConsumptionPoint.from_api(d) for d in days_raw if isinstance(d, dict)
            ],
            raw=payload,
        )


@dataclass(slots=True)
class YearlyConsumptionPoint:
    """One year in the `yearly-consumption` time series.

    Field names mirror the per-day shape; for a year point
    `totalConsumption` is the annual cubic-metre figure (per the SPA's
    YEAR view that prefers `totalConsumption` over `totalLitres`).
    """

    year: int | None
    total_consumption_litres: float | None
    total_consumption_m3: float | None
    total_cost: float | None
    total_cost_including_sewerage: float | None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> YearlyConsumptionPoint:
        """Build a `YearlyConsumptionPoint` from the raw API payload."""
        return cls(
            year=_coerce_int(payload.get("year")),
            total_consumption_litres=_coerce_float(payload.get("totalLitres")),
            total_consumption_m3=_coerce_float(payload.get("totalConsumption")),
            total_cost=_coerce_float(payload.get("totalCost")),
            total_cost_including_sewerage=_coerce_float(
                payload.get("totalCostIncludingSewerage"),
            ),
            raw=payload,
        )


@dataclass(slots=True)
class Customer:
    """Response shape of GET /api/account/customer/detail.

    Yorkshire Water do not expose a single customer ID at this endpoint;
    the human-readable account number is per-property and lives on
    `Property.display_account_reference`.
    """

    title: str | None
    forename: str | None
    surname: str | None
    email: str | None
    mobile_telephone: str | None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> Customer:
        """Build a `Customer` from the raw API payload."""
        mobile = payload.get("mobileTelephone")
        if isinstance(mobile, str):
            mobile = mobile.strip() or None
        return cls(
            title=payload.get("title") or None,
            forename=payload.get("forename") or None,
            surname=payload.get("surname") or None,
            email=payload.get("email") or None,
            mobile_telephone=mobile,
            raw=payload,
        )

    @property
    def full_name(self) -> str:
        """Return `Title Forename Surname` joined, falling back to email."""
        parts = [self.title, self.forename, self.surname]
        joined = " ".join(p for p in parts if p)
        return joined or self.email or ""


@dataclass(slots=True)
class Address:
    """Postal address for a Yorkshire Water property."""

    house_name: str | None
    house_number: str | None
    address_line_1: str | None
    address_line_2: str | None
    address_line_3: str | None
    address_line_4: str | None
    address_line_5: str | None
    postcode: str | None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, payload: dict[str, Any] | None) -> Address:
        """Build an `Address` from the raw API payload (None becomes empty)."""
        payload = payload or {}
        def _norm(value: Any) -> str | None:
            if isinstance(value, str):
                value = value.strip()
                return value or None
            return None

        return cls(
            house_name=_norm(payload.get("houseName")),
            house_number=_norm(payload.get("houseNumber")),
            address_line_1=_norm(payload.get("addressLine1")),
            address_line_2=_norm(payload.get("addressLine2")),
            address_line_3=_norm(payload.get("addressLine3")),
            address_line_4=_norm(payload.get("addressLine4")),
            address_line_5=_norm(payload.get("addressLine5")),
            postcode=_norm(payload.get("postcode")),
            raw=payload,
        )

    def formatted(self) -> str:
        """Return a comma-separated single-line address string.

        Combines house name + house number on the first line where both
        are present (e.g. `Rose Cottage 1 Smith Street`), then any
        present address lines and the postcode. Empty entries are
        skipped, the result has no trailing comma.
        """
        first: list[str] = []
        if self.house_name:
            first.append(self.house_name)
        if self.house_number:
            first.append(self.house_number)
        first_line = " ".join(first)
        components = [
            first_line or None,
            self.address_line_1,
            self.address_line_2,
            self.address_line_3,
            self.address_line_4,
            self.address_line_5,
            self.postcode,
        ]
        return ", ".join(c for c in components if c)


@dataclass(slots=True)
class Property:
    """One entry in the `properties` array of GET /api/account/properties.

    `account_reference` is the long opaque token used to scope per-property
    API calls (passed as `?accountReference=...`).
    `display_account_reference` is the 16-digit human-readable account
    number Yorkshire Water print on bills and we surface in the UI.
    """

    account_reference: str
    display_account_reference: str
    account_status: str | None
    address: Address
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> Property:
        """Build a `Property` from the raw API payload."""
        return cls(
            account_reference=payload.get("accountReference") or "",
            display_account_reference=payload.get("displayAccountReference") or "",
            account_status=payload.get("accountStatus") or None,
            address=Address.from_api(payload.get("address")),
            raw=payload,
        )

    @property
    def is_live(self) -> bool:
        """True when YW reports the account is live (billing-active)."""
        return (self.account_status or "").lower() == "live"


@dataclass(slots=True)
class PropertiesPage:
    """Response shape of GET /api/account/properties.

    The customer portal pages this endpoint with `?page=N&pageSize=10`.
    We expose the page metadata so callers can iterate through all
    pages; `YorkshireWaterClient.iter_properties()` does that for you.
    """

    properties: list[Property]
    total_properties: int
    total_pages: int
    page_number: int
    account_disabled: bool
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> PropertiesPage:
        """Build a `PropertiesPage` from the raw API payload."""
        items_raw = payload.get("properties") or []
        if not isinstance(items_raw, list):
            items_raw = []
        return cls(
            properties=[
                Property.from_api(p) for p in items_raw if isinstance(p, dict)
            ],
            total_properties=_coerce_int(payload.get("totalProperties")) or 0,
            total_pages=_coerce_int(payload.get("totalPages")) or 0,
            page_number=_coerce_int(payload.get("pageNumber")) or 0,
            account_disabled=bool(payload.get("accountDisabled", False)),
            raw=payload,
        )


@dataclass(slots=True)
class TokenSet:
    """An OAuth access token and its companion refresh token.

    The token fields are excluded from the dataclass repr to avoid leaking
    secrets through casual logging.
    """

    access_token: str = field(repr=False)
    refresh_token: str | None = field(repr=False)
    token_type: str
    expires_at: datetime
    scope: str | None = None

    def is_expired(self, leeway_seconds: float = 0.0) -> bool:
        """Return True if the token is past its expiry, optionally with leeway."""
        now = datetime.now(UTC)
        return (self.expires_at - now).total_seconds() <= leeway_seconds
