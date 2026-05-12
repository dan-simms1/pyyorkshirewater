"""Async Python client for Yorkshire Water's customer self-service API.

This library is unofficial and not affiliated with Yorkshire Water Services
Limited. It exists to let customers access their own smart meter data through
the same backend that powers `my.yorkshirewater.com`.

Authentication uses cookie-based silent renewal against the IdentityServer at
`login.yorkshirewater.com`. The user logs in once via their own browser
(reCAPTCHA passes naturally there) and exports the session cookie; the
library uses it to mint fresh access tokens via
`/connect/authorize?prompt=none` whenever needed.
"""

from __future__ import annotations

from .auth import (
    Authenticator,
    CookieSessionExpiredError,
)
from .client import YorkshireWaterClient
from .const import IDP_COOKIE_DOMAIN
from .exceptions import (
    YorkshireWaterAPIError,
    YorkshireWaterAuthError,
    YorkshireWaterError,
    YorkshireWaterMeterNotReadyError,
    YorkshireWaterRateLimitError,
)
from .models import (
    Address,
    ContinuousFlowAlarm,
    CurrentConsumption,
    Customer,
    DailyConsumptionPoint,
    MeterDetails,
    MeterStatus,
    PropertiesPage,
    Property,
    TokenSet,
    UsagePeriod,
    YearlyConsumptionPoint,
)

__version__ = "0.4.0"

__all__ = [
    "IDP_COOKIE_DOMAIN",
    "Address",
    "Authenticator",
    "ContinuousFlowAlarm",
    "CookieSessionExpiredError",
    "CurrentConsumption",
    "Customer",
    "DailyConsumptionPoint",
    "MeterDetails",
    "MeterStatus",
    "PropertiesPage",
    "Property",
    "TokenSet",
    "UsagePeriod",
    "YearlyConsumptionPoint",
    "YorkshireWaterAPIError",
    "YorkshireWaterAuthError",
    "YorkshireWaterClient",
    "YorkshireWaterError",
    "YorkshireWaterMeterNotReadyError",
    "YorkshireWaterRateLimitError",
    "__version__",
]
