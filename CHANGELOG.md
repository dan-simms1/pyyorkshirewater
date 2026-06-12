# Changelog

All notable changes to `pyyorkshirewater` are recorded here. The project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.4.0] - 2026-06-13

### Fixed

- **`/smartmeter/daily-consumption` finally works**. Three previously
  undiscovered required query parameters land the endpoint: `moveInDate`
  (the customer's account start date at the property - exposed by the
  API as `meter-details.startDate`, despite the name actually being the
  account open date and not when the smart meter was physically
  installed), `moveOutDate` (today for active customers) and
  `timePeriod=1`. Captured by watching the SPA's request when the user
  clicks "View usage in detail". Without these the endpoint returned
  `400 "Invalid date range."` for every other parameter combination.

### Changed (breaking)

- `get_daily_consumption(start_date, end_date, move_in_date,
  move_out_date, time_period=1, meter_reference=)` replaces the
  previous `(start_date, end_date, unit, meter_reference)` signature.
  `unit` is dropped (the endpoint ignores it). `move_in_date` defaults
  to the cached meter's `start_date` and `move_out_date` defaults to
  today (UTC), so the common single-property call shortens to just
  `start_date=, end_date=`.

- `DailyConsumptionPoint` rewritten to mirror the real per-day shape
  captured on 2026-06-13: drops `total_consumption_m3`, renames
  `cleanWaterCost` → `standardTariffCleanWaterCost`, adds `sewerage_cost`
  (from `standardTariffSewerageCost`) and `is_missing` (from
  `isMissingConsumption`). `total_cost` becomes an alias for
  `total_cost_including_sewerage` so callers reading the older name
  keep working.

## [1.3.0] - 2026-06-12

### Changed (breaking)

- **`UsagePeriod` rewritten to match the real API shape**. The previous
  fields (`period_total_litres`, `daily_litres_average`, `daily_points`
  etc.) were inferred from the SPA bundle and did not match what
  `/your-usage` actually returns. Replaced with the empirically-verified
  fields: `month`, `total_consumption_litres`, `clean_water_cost`,
  `sewerage_cost`, `total_cost_including_sewerage`, `estimated_day_count`,
  `missing_day_count`. Each entry in the array is now a single month's
  summary.

- **`YearlyConsumptionPoint` renamed and rewritten as `YearlyConsumption`**.
  The endpoint returns one summary object per year, not an array of
  yearly points. `get_yearly_consumption(...)` now takes a required
  `year: int` query parameter and returns `YearlyConsumption | None`.
  The new model exposes year-to-date totals, monthly averages, and the
  monthly breakdown (`monthly_consumption: list[UsagePeriod]`).

- The `unit` keyword on `get_yearly_consumption` is removed; the endpoint
  ignores it.

### Added

- `CurrentConsumption.latest_data_date` and `.latest_update_date`
  (`date | None`), parsed from the API's US-style "M/D/YYYY" strings.
  Used by Home Assistant to surface a "last reading" timestamp sensor
  without depending on the still-unsolved `/daily-consumption` endpoint.

- `ContinuousFlowAlarm.continuous_flow_l_per_h` (leak rate) and
  `.cost_per_day` (projected daily cost while leak is active).

### Notes

- `/daily-consumption` remains an open problem: every documented
  parameter shape (single date, range, US format, alt names) returns
  `400 "Invalid date range."`. Probing continues out-of-band; for now
  `get_daily_consumption` is unchanged from 1.2 but unlikely to
  succeed against the live API.

## [1.2.0] - 2026-06-12

### Fixed

- **Consumption endpoints now send the correct query parameter**. The
  four consumption methods (`get_current_consumption`, `get_your_usage`,
  `get_daily_consumption`, `get_yearly_consumption`) were sending
  `accountReference=` on the wire, but the server requires
  `meterReference=` on these endpoints. The previous behaviour returned
  HTTP 400 from the API as soon as the path bug from 1.1.0 was fixed.
  Discovered against a live meter on 2026-06-12 after upgrading to
  1.1.0 and seeing `400 Bad Request` in the integration logs.

### Changed

- **`account_reference=` kwarg removed from the four consumption
  methods.** It was always being sent as the wrong query parameter, so
  no caller could have been depending on it producing correct results.
  Replaced with `meter_reference=` (the 10-digit meter reference the
  server actually wants). By default the methods use the meter
  reference cached from the most recent `get_meter_details()` call
  (which `login()` runs automatically). For multi-property accounts,
  call `get_meter_details(account_reference=...)` per property and pass
  the returned `meter_reference` explicitly.

## [1.1.0] - 2026-06-12

### Fixed

- **Smart-meter endpoint paths corrected**. The five `/smartmeter/*`
  endpoint constants were missing the `/account/` URL segment. The
  client was calling `https://my.yorkshirewater.com/api/smartmeter/...`,
  which 404s; the SPA actually calls `/api/account/smartmeter/...`. The
  `get_meter_details` and `get_current_consumption` methods catch 404
  and return empty objects, so the bug surfaced as `MeterStatus.NO_METER`
  for every metered customer rather than as a visible error. Discovered
  by capturing the SPA's XHR traffic against a live meter.

### Operational notes

- Anyone running v1.0.0 saw `MeterStatus.NO_METER` regardless of meter
  state. After upgrading, callers may immediately get a `LIVE` meter
  status and the previously silent consumption endpoints will start
  returning data.

## [1.0.0] - 2026-05-12

First stable public release.

### Features

- **Async Python client** for the Yorkshire Water customer self-service API
  (`my.yorkshirewater.com`).
- **Cookie-based authentication** via Duende IdentityServer's silent-renewal
  flow. Yorkshire Water's SPA OAuth client does not permit the password grant,
  the device flow or `offline_access`; the only workable path is to drive a
  real browser through the login form, capture the session cookie, and mint
  access tokens via `/connect/authorize?prompt=none`. This library wraps that
  cookie-driven flow with type-safe Python.
- **Customer and Property data model**. Single- and multi-property accounts
  supported via the `account_reference=` scoping kwarg on all smart-meter
  endpoints.
- **Three-state meter readiness model** (`NO_METER`, `PENDING_ACTIVATION`,
  `LIVE`) reflecting the Yorkshire Water smart-meter rollout (2025-2030).
  Consumption endpoints raise `YorkshireWaterMeterNotReadyError` against
  pre-LIVE meters.
- **Endpoints**: `get_customer`, `iter_properties`, `get_meter_details`,
  `get_current_consumption`, `get_your_usage`, `get_daily_consumption`,
  `get_yearly_consumption`, with explicit error types for each failure mode.
- **Type hints throughout**; mypy strict-compatible.
- **Python 3.11+**.

### Operational notes

- Cookies typically live several hours to several weeks depending on
  Yorkshire Water's IdentityServer configuration. The library handles
  expiry via `CookieSessionExpiredError` (a subclass of
  `YorkshireWaterAuthError`) so callers can prompt for re-auth.
- Logs to a named logger `pyyorkshirewater` at DEBUG level.

### Disclaimer

Unofficial. Not affiliated with Yorkshire Water Services Limited. Use only
against accounts you own.
