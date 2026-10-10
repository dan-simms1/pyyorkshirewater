"""Unit tests for `pyyorkshirewater.models`."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from pyyorkshirewater.models import (
    Address,
    ContinuousFlowAlarm,
    CurrentConsumption,
    Customer,
    DailyConsumptionPoint,
    MeterDetails,
    MeterStatus,
    PropertiesPage,
    TokenSet,
    UsagePeriod,
    YearlyConsumption,
)


def test_meter_details_parses_iso_dates() -> None:
    payload = {
        "meterReference": "ABC-123",
        "startDate": "2026-04-01",
        "endDate": "2027-04-01",
        "currentDate": "2026-05-06",
    }
    details = MeterDetails.from_api(payload)

    assert details.meter_reference == "ABC-123"
    assert details.start_date == date(2026, 4, 1)
    assert details.end_date == date(2027, 4, 1)
    assert details.current_date == date(2026, 5, 6)
    assert details.raw == payload


def test_meter_details_handles_missing_fields() -> None:
    details = MeterDetails.from_api({})

    assert details.meter_reference is None
    assert details.start_date is None
    assert details.end_date is None
    assert details.current_date is None


def test_current_consumption_with_active_alarm() -> None:
    payload = {
        "isMeterBau": True,
        "currentContinuousFlowAlarmState": True,
        "currentContinuousFlowAlarmDetails": [
            {"alarmStartDate": "2026-05-05T03:00:00Z"},
            {"alarmStartDate": "2026-05-06T03:00:00+00:00"},
        ],
    }
    consumption = CurrentConsumption.from_api(payload)

    assert consumption.is_meter_bau is True
    assert consumption.continuous_flow_alarm_state is True
    assert len(consumption.continuous_flow_alarm_details) == 2
    first_alarm = consumption.continuous_flow_alarm_details[0]
    assert first_alarm.alarm_start == datetime(2026, 5, 5, 3, 0, tzinfo=UTC)


def test_current_consumption_handles_missing_alarms() -> None:
    consumption = CurrentConsumption.from_api({"isMeterBau": False})

    assert consumption.is_meter_bau is False
    assert consumption.continuous_flow_alarm_state is False
    assert consumption.continuous_flow_alarm_details == []


def test_continuous_flow_alarm_handles_invalid_date() -> None:
    alarm = ContinuousFlowAlarm.from_api({"alarmStartDate": "not-a-date"})
    assert alarm.alarm_start is None


def test_continuous_flow_alarm_sentinel_date_is_none() -> None:
    """YW sends "1/1/0001" when no alarm is active (seen 2026-10-10)."""
    alarm = ContinuousFlowAlarm.from_api({
        "alarmStartDate": "1/1/0001",
        "continuousFlowLperH": "0",
        "continuousFlowStandardTariffCostPerDay": "0",
    })
    assert alarm.alarm_start is None
    assert alarm.continuous_flow_l_per_h == 0
    assert alarm.cost_per_day == 0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10/9/2026", datetime(2026, 10, 9)),
        ("10/9/2026 3:15:00 AM", datetime(2026, 10, 9, 3, 15)),
        ("10/9/2026 3:15:00 PM", datetime(2026, 10, 9, 15, 15)),
        ("10/9/2026 15:15", datetime(2026, 10, 9, 15, 15)),
    ],
)
def test_continuous_flow_alarm_parses_us_dates(raw: str, expected: datetime) -> None:
    assert ContinuousFlowAlarm.from_api({"alarmStartDate": raw}).alarm_start == expected


def test_daily_consumption_point_parses_full_payload() -> None:
    """Empirically verified shape captured on 2026-06-13 against a live meter."""
    point = DailyConsumptionPoint.from_api({
        "date": "2026-06-10",
        "totalConsumptionLitres": "741",
        "standardTariffCleanWaterCost": "1.57",
        "standardTariffSewerageCost": "1.90",
        "totalCostIncludingSewerage": "3.47",
        "isEstimatedConsumption": False,
        "isMissingConsumption": False,
        "continuousFlowAlarm": False,
    })
    assert point.point_date == date(2026, 6, 10)
    assert point.total_consumption_litres == pytest.approx(741)
    assert point.clean_water_cost == pytest.approx(1.57)
    assert point.sewerage_cost == pytest.approx(1.90)
    assert point.total_cost_including_sewerage == pytest.approx(3.47)
    # `total_cost` is an alias for total_cost_including_sewerage for
    # callers that read the older field name.
    assert point.total_cost == pytest.approx(3.47)
    assert point.is_estimated is False
    assert point.is_missing is False
    assert point.continuous_flow_alarm is False


def test_daily_consumption_point_handles_missing() -> None:
    point = DailyConsumptionPoint.from_api({})
    assert point.point_date is None
    assert point.total_consumption_litres is None
    assert point.clean_water_cost is None
    assert point.sewerage_cost is None
    assert point.total_cost is None
    assert point.is_estimated is False
    assert point.is_missing is False
    assert point.continuous_flow_alarm is False


def test_daily_consumption_point_preserves_zero() -> None:
    """A legitimate zero must not be coerced to None by truthiness checks."""
    point = DailyConsumptionPoint.from_api({
        "date": "2026-05-05",
        "totalConsumptionLitres": 0,
    })
    assert point.total_consumption_litres == 0.0


def test_daily_consumption_point_handles_empty_string() -> None:
    """Empty strings (which the SPA defaults to) coerce to None."""
    point = DailyConsumptionPoint.from_api({
        "date": "2026-05-05",
        "totalConsumptionLitres": "",
    })
    assert point.total_consumption_litres is None


def test_daily_consumption_point_accepts_alt_sewerage_field_names() -> None:
    """The SPA bundle exposes three spellings of the same field. Accept all."""
    p1 = DailyConsumptionPoint.from_api({"totalCostIncludingSewerage": "1"})
    p2 = DailyConsumptionPoint.from_api({"totalCostInclSewerage": "1"})
    p3 = DailyConsumptionPoint.from_api({"totalCostIncSewerage": "1"})
    assert p1.total_cost_including_sewerage == 1.0
    assert p2.total_cost_including_sewerage == 1.0
    assert p3.total_cost_including_sewerage == 1.0


def test_yearly_consumption_preserves_zero() -> None:
    summary = YearlyConsumption.from_api({"year": 2025, "totalConsumption": 0})
    assert summary.total_consumption_litres == 0.0


def test_continuous_flow_alarm_skips_non_dict_entries() -> None:
    """Mixed lists are filtered to just dict entries."""
    consumption = CurrentConsumption.from_api(
        {
            "isMeterBau": True,
            "currentContinuousFlowAlarmState": True,
            "currentContinuousFlowAlarmDetails": [
                {"alarmStartDate": "2026-05-05T03:00:00Z"},
                "ignored",
                None,
                42,
            ],
        },
    )
    assert len(consumption.continuous_flow_alarm_details) == 1


def test_continuous_flow_alarm_handles_dict_envelope() -> None:
    """A dict-shaped alarm details payload is treated as a single alarm."""
    consumption = CurrentConsumption.from_api(
        {
            "isMeterBau": True,
            "currentContinuousFlowAlarmState": True,
            "currentContinuousFlowAlarmDetails": {"alarmStartDate": "2026-05-05T03:00:00Z"},
        },
    )
    assert len(consumption.continuous_flow_alarm_details) == 1


def test_token_set_repr_does_not_leak_secrets() -> None:
    """The TokenSet repr must not include access or refresh token values."""
    tokens = TokenSet(
        access_token="super-secret-access",
        refresh_token="super-secret-refresh",
        token_type="Bearer",
        expires_at=datetime.now(UTC) + timedelta(seconds=3600),
    )
    representation = repr(tokens)
    assert "super-secret-access" not in representation
    assert "super-secret-refresh" not in representation


def test_yearly_consumption_parses_real_api_shape() -> None:
    """Empirically verified shape captured on 2026-06-12 against a live meter."""
    summary = YearlyConsumption.from_api({
        "year": 2026,
        "meterReference": "WAKE-001",
        "totalConsumption": 26315,
        "totalCost": 123.13,
        "totalStandardTariffCleanWaterCost": 55.57,
        "totalStandardTariffSewerageCost": 67.56,
        "monthlyLitresAverageForYear": 4385.83,
        "monthlyCostAverageForYear": 9.26,
        "monthlyConsumption": [
            {"month": "06", "totalConsumptionLitres": "8055"},
            {"month": "05", "totalConsumptionLitres": "18260"},
        ],
    })
    assert summary.year == 2026
    assert summary.meter_reference == "WAKE-001"
    assert summary.total_consumption_litres == pytest.approx(26315)
    assert summary.total_cost == pytest.approx(123.13)
    assert summary.total_clean_water_cost == pytest.approx(55.57)
    assert summary.total_sewerage_cost == pytest.approx(67.56)
    assert summary.monthly_litres_average == pytest.approx(4385.83)
    assert summary.monthly_cost_average == pytest.approx(9.26)
    assert len(summary.monthly_consumption) == 2
    assert summary.monthly_consumption[0].month == "06"


def test_usage_period_parses_monthly_summary() -> None:
    """Empirically verified shape captured on 2026-06-12 against a live meter."""
    payload = {
        "month": "06",
        "totalConsumptionLitres": "8055",
        "standardTariffCleanWaterCost": 17.02,
        "standardTariffSewerageCost": 20.69,
        "estimatedDayCount": 3,
        "missingDayCount": 0,
        "totalCostIncludingSewerage": 37.71,
    }
    period = UsagePeriod.from_api(payload)
    assert period.month == "06"
    assert period.total_consumption_litres == pytest.approx(8055)
    assert period.clean_water_cost == pytest.approx(17.02)
    assert period.sewerage_cost == pytest.approx(20.69)
    assert period.total_cost_including_sewerage == pytest.approx(37.71)
    assert period.estimated_day_count == 3
    assert period.missing_day_count == 0
    assert period.raw == payload


def test_token_set_is_expired_returns_true_when_past() -> None:
    tokens = TokenSet(
        access_token="a",
        refresh_token=None,
        token_type="Bearer",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    assert tokens.is_expired() is True


def test_token_set_is_expired_respects_leeway() -> None:
    tokens = TokenSet(
        access_token="a",
        refresh_token=None,
        token_type="Bearer",
        expires_at=datetime.now(UTC) + timedelta(seconds=30),
    )
    assert tokens.is_expired(leeway_seconds=60) is True
    assert tokens.is_expired(leeway_seconds=10) is False


def test_meter_status_values() -> None:
    assert MeterStatus.NO_METER.value == "no_meter"
    assert MeterStatus.PENDING_ACTIVATION.value == "pending_activation"
    assert MeterStatus.LIVE.value == "live"


def test_meter_details_unparseable_date_is_none() -> None:
    details = MeterDetails.from_api({"meterReference": "ABC-123", "startDate": "soon"})
    assert details.start_date is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10/10/2026", date(2026, 10, 10)),
        ("2026-10-10", date(2026, 10, 10)),
        ("2026-10-10T00:00:00+00:00", date(2026, 10, 10)),
        ("not a date", None),
        ("", None),
        (None, None),
    ],
)
def test_current_consumption_latest_dates(raw: object, expected: date | None) -> None:
    consumption = CurrentConsumption.from_api({"latestDataDate": raw, "latestUpdateDate": raw})
    assert consumption.latest_data_date == expected
    assert consumption.latest_update_date == expected


@pytest.mark.parametrize("raw", [None, "", "   ", 20261010])
def test_continuous_flow_alarm_missing_date(raw: object) -> None:
    assert ContinuousFlowAlarm.from_api({"alarmStartDate": raw}).alarm_start is None


def test_current_consumption_ignores_bad_alarm_details() -> None:
    consumption = CurrentConsumption.from_api({"currentContinuousFlowAlarmDetails": "x"})
    assert consumption.continuous_flow_alarm_details == []


def test_numeric_coercion_tolerates_junk() -> None:
    period = UsagePeriod.from_api({
        "month": "  ",
        "totalConsumptionLitres": "lots",
        "estimatedDayCount": "two",
        "missingDayCount": "",
    })
    assert period.month is None
    assert period.total_consumption_litres is None
    assert period.estimated_day_count is None
    assert period.missing_day_count is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("true", True), (" Yes ", True), ("1", True), ("false", False), ("no", False),
     (1, True), (0, False), (None, False)],
)
def test_daily_point_flags_coerce(raw: object, expected: bool) -> None:
    point = DailyConsumptionPoint.from_api({"date": "2026-10-09", "isMissingConsumption": raw})
    assert point.is_missing is expected


def test_yearly_consumption_ignores_bad_monthly_list() -> None:
    yearly = YearlyConsumption.from_api({"year": 2026, "monthlyConsumption": {"10": 1}})
    assert yearly.monthly_consumption == []


def test_customer_full_name_falls_back_to_email() -> None:
    customer = Customer.from_api({"email": "user@example.com", "mobileTelephone": "  "})
    assert customer.full_name == "user@example.com"
    assert customer.mobile_telephone is None


def test_address_first_line_variants() -> None:
    named = Address.from_api({"houseName": "Rose Cottage", "addressLine1": "Example Street",
                              "postcode": "EX1 1EX"})
    both = Address.from_api({"houseName": "Rose Cottage", "houseNumber": "1",
                             "addressLine1": "Example Street"})
    bare = Address.from_api({"addressLine1": "Example Street", "postcode": 123})
    empty = Address.from_api(None)

    assert named.formatted() == "Rose Cottage, Example Street, EX1 1EX"
    assert both.formatted() == "Rose Cottage 1, Example Street"
    assert bare.formatted() == "Example Street"
    assert empty.formatted() == ""


def test_property_status_and_page_parsing() -> None:
    page = PropertiesPage.from_api({
        "properties": [
            {"accountReference": "opaque-ref-1", "displayAccountReference": "1234567890123456",
             "accountStatus": "Closed"},
            "not-a-property",
        ],
        "totalProperties": "1",
        "totalPages": None,
        "accountDisabled": True,
    })
    assert len(page.properties) == 1
    assert page.properties[0].is_live is False
    assert page.total_properties == 1
    assert page.total_pages == 0
    assert page.account_disabled is True
    assert PropertiesPage.from_api({"properties": "x"}).properties == []
