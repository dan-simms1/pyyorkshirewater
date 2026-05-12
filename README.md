# pyyorkshirewater

Async Python client for the Yorkshire Water customer self-service API.

This library is unofficial and not affiliated with Yorkshire Water. It exists
so that customers can read their own smart meter consumption data from the
same backend that powers `my.yorkshirewater.com`.

## Status

Early alpha. The smart meter rollout is in progress across the Yorkshire
Water region between 2025 and 2030, so most accounts will not yet have a live
meter. Until a meter is live the client will report a `MeterStatus` of
`NO_METER` or `PENDING_ACTIVATION`, and the consumption endpoints
(`get_your_usage`, `get_daily_consumption`, `get_yearly_consumption`) will
raise `YorkshireWaterMeterNotReadyError`. The readiness probes
(`get_meter_details`, `get_current_consumption`) work in every state.

## Why cookies, not email and password

Yorkshire Water's portal SPA uses Duende IdentityServer with authorization
code plus PKCE only. The SPA OAuth client (`css-onlineaccount-fe`) is not
permitted to use the password grant, the device flow or `offline_access`,
and the login form is protected by invisible Google reCAPTCHA v3 which a
non-browser HTTP client cannot pass.

The workable architecture is therefore: the user logs in to
my.yorkshirewater.com once in their own browser, where reCAPTCHA succeeds
naturally, and exports the IdentityServer session cookie. This library uses
that cookie to drive `/connect/authorize?prompt=none` for silent renewal,
mints fresh access tokens on demand and never sees the user's password.

The companion Chrome extension (separate repository) makes cookie export a
single click.

## Install

```bash
pip install pyyorkshirewater
```

Python 3.11 or newer.

## Quick start

```python
import asyncio
from pyyorkshirewater import YorkshireWaterClient, MeterStatus

cookies = {
    "idsrv": "<value from a logged-in browser session>",
    "idsrv.session": "<value from a logged-in browser session>",
    # any other login.yorkshirewater.com cookies your browser holds
}

async def main():
    async with YorkshireWaterClient(cookies=cookies) as client:
        await client.login()

        if client.meter_status is MeterStatus.LIVE:
            daily = await client.get_daily_consumption()
            print(daily)
        else:
            print(f"Meter is {client.meter_status.value}, no data yet.")

asyncio.run(main())
```

## Cookie lifetime

The IdentityServer session cookie typically lives between several hours and
several weeks depending on the provider's configuration. Each silent renewal
call may extend it (sliding expiration) on servers configured for that. When
the cookie eventually expires, the library raises
`CookieSessionExpiredError` (a subclass of `YorkshireWaterAuthError`) with
`error="login_required"`. Callers should catch this and prompt the user for
fresh cookies. The Home Assistant integration handles this via a reauth
flow.

## Meter readiness states

Yorkshire Water's portal exposes a three-state model. The library mirrors it
through the `MeterStatus` enum:

- `NO_METER`: the account has no smart meter on it.
- `PENDING_ACTIVATION`: a meter is registered but is awaiting network setup
  by Yorkshire Water.
- `LIVE`: the meter is reporting data and the consumption endpoints will
  return real values.

Application code should check `client.meter_status` before requesting
consumption data.

## Logging

The library logs to a named logger called `pyyorkshirewater` at the `DEBUG`
level. Configure it in the usual way:

```python
import logging
logging.getLogger("pyyorkshirewater").setLevel(logging.DEBUG)
```

## Errors

All errors raised by the library inherit from `YorkshireWaterError`. The
specific subclasses are:

- `YorkshireWaterAuthError`: silent renewal failed for a reason that is not
  cookie expiry. The original IdentityServer `error` and `error_description`
  are exposed on the exception.
- `CookieSessionExpiredError` (subclass of `YorkshireWaterAuthError`): the
  IdP session cookie is expired or invalid. Re-export from a fresh browser
  session.
- `YorkshireWaterAPIError`: a non-auth HTTP error from the customer API.
- `YorkshireWaterMeterNotReadyError`: raised when a consumption endpoint is
  called against a meter that is not yet `LIVE`.
- `YorkshireWaterRateLimitError`: 429 response with optional `retry_after`.

## Related projects

- [`dan-simms1/ha-yorkshire-water`](https://github.com/dan-simms1/ha-yorkshire-water):
  Home Assistant integration that uses this library.
- (forthcoming) Chrome extension that extracts the cookies needed by this
  library and the integration with one click.

## Disclaimer

This project is unofficial and not affiliated with, endorsed by or supported
by Yorkshire Water Services Limited. Trademarks are the property of their
respective owners. Use this software at your own risk and only against
accounts that you own.

## Licence

MIT. See [LICENSE](LICENSE).
