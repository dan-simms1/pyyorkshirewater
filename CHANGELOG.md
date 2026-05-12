# Changelog

All notable changes to `pyyorkshirewater` are recorded here. The project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
