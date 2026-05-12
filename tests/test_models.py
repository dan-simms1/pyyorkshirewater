"""Unit tests for `pyyorkshirewater.models`."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from pyyorkshirewater.models import (
    ContinuousFlowAlarm,
    CurrentConsumption,
    DailyConsumptionPoint,
    MeterDetails,
    MeterStatus,
    TokenSet,
    UsagePeriod,
    YearlyConsumptionPoint,
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


def test_daily_consumption_point_parses_full_payload() -> None:
    point = DailyConsumptionPoint.from_api({
        "date": "2026-05-05",
        "totalConsumptionLitres": "123.4",
        "totalConsumption": "0.1234",
        "totalCost": "45.67",
        "totalCostIncludingSewerage": "78.9",
        "cleanWaterCost": "12.3",
        "isEstimatedConsumption": True,
        "continuousFlowAlarm": False,
    })
    assert point.point_date == date(2026, 5, 5)
    assert point.total_consumption_litres == pytest.approx(123.4)
    assert point.total_consumption_m3 == pytest.approx(0.1234)
    assert point.total_cost == pytest.approx(45.67)
    assert point.total_cost_including_sewerage == pytest.approx(78.9)
    assert point.clean_water_cost == pytest.approx(12.3)
    assert point.is_estimated is True
    assert point.continuous_flow_alarm is False


def test_daily_consumption_point_handles_missing() -> None:
    point = DailyConsumptionPoint.from_api({})
    assert point.point_date is None
    assert point.total_consumption_litres is None
    assert point.total_consumption_m3 is None
    assert point.total_cost is None
    assert point.is_estimated is False
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


def test_yearly_consumption_point_preserves_zero() -> None:
    point = YearlyConsumptionPoint.from_api({"year": 2025, "totalLitres": 0})
    assert point.total_consumption_litres == 0.0


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


def test_yearly_consumption_point() -> None:
    point = YearlyConsumptionPoint.from_api({
        "year": "2026",
        "totalLitres": "4567",
        "totalConsumption": "4.567",
        "totalCost": "1234.56",
    })
    assert point.year == 2026
    assert point.total_consumption_litres == pytest.approx(4567)
    assert point.total_consumption_m3 == pytest.approx(4.567)
    assert point.total_cost == pytest.approx(1234.56)


def test_usage_period_parses_period_totals_and_days() -> None:
    payload = {
        "totalLitres": "10000",
        "totalConsumption": "10.0",
        "totalCost": "4523.0",
        "totalCostIncludingSewerage": "7800.0",
        "totalStandardTariffCleanWaterCost": "4523.0",
        "totalStandardTariffSewerageCost": "3277.0",
        "dailyLitresAverage": "333.3",
        "dailyCostAverage": "150.7",
        "dailyValues": [
            {"date": "2026-05-04", "totalConsumptionLitres": 100},
            {"date": "2026-05-05", "totalConsumptionLitres": 110},
            "ignored",
            None,
        ],
    }
    period = UsagePeriod.from_api(payload)
    assert period.period_total_litres == pytest.approx(10000)
    assert period.period_total_consumption_m3 == pytest.approx(10.0)
    assert period.period_total_cost == pytest.approx(4523.0)
    assert period.period_total_cost_including_sewerage == pytest.approx(7800.0)
    assert period.period_total_clean_water_cost == pytest.approx(4523.0)
    assert period.period_total_sewerage_cost == pytest.approx(3277.0)
    assert period.daily_litres_average == pytest.approx(333.3)
    assert period.daily_cost_average == pytest.approx(150.7)
    # The two non-dict entries in dailyValues are filtered out.
    assert len(period.daily_points) == 2
    assert period.daily_points[0].total_consumption_litres == 100
    assert period.daily_points[1].total_consumption_litres == 110
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
