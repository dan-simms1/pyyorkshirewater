"""Constants used across pyyorkshirewater.

The values here come from the public OIDC discovery document at
https://login.yorkshirewater.com/.well-known/openid-configuration and from
static analysis of the customer portal bundle at https://my.yorkshirewater.com.

Anything that has not been confirmed against the live API is annotated with an
ASSUMPTION comment so that future maintainers know where to verify.
"""

from __future__ import annotations

from typing import Final

PACKAGE_NAME: Final = "pyyorkshirewater"

# IdentityServer endpoints. Confirmed against the public discovery document.
IDP_BASE_URL: Final = "https://login.yorkshirewater.com"
AUTHORIZE_ENDPOINT: Final = f"{IDP_BASE_URL}/connect/authorize"
TOKEN_ENDPOINT: Final = f"{IDP_BASE_URL}/connect/token"
REVOCATION_ENDPOINT: Final = f"{IDP_BASE_URL}/connect/revocation"
USERINFO_ENDPOINT: Final = f"{IDP_BASE_URL}/connect/userinfo"
END_SESSION_ENDPOINT: Final = f"{IDP_BASE_URL}/connect/endsession"

# OAuth client. Extracted from the SPA bundle. The SPA client only allows
# authorization_code with PKCE: ROPC, device_code and offline_access are all
# rejected. The library therefore performs silent renewal via
# `/connect/authorize?prompt=none` using the IdP session cookie that the user
# obtains by logging in to my.yorkshirewater.com in their own browser.
DEFAULT_CLIENT_ID: Final = "css-onlineaccount-fe"
DEFAULT_REDIRECT_URI: Final = "https://my.yorkshirewater.com/account/callback/response"
DEFAULT_SCOPES: Final = "openid user-names css-onlineaccount-api css-registration-api"

# Domain on which the IdentityServer session cookies live. Only cookies for
# this domain are needed by the library.
IDP_COOKIE_DOMAIN: Final = "login.yorkshirewater.com"

# Customer self-service API. All authenticated endpoints sit under this base URL.
API_BASE_URL: Final = "https://my.yorkshirewater.com/api"

# Smart meter endpoints under API_BASE_URL.
#
# Smart meter endpoints sit at `/api/smartmeter/...` and are scoped to
# the customer's "current" property by the server. For multi-property
# accounts the scope is selected via the optional `accountReference`
# query parameter (the long opaque token, not the human-readable
# display number). The library exposes that scope as an
# `account_reference=` keyword argument on each method.
ENDPOINT_METER_DETAILS: Final = "/smartmeter/meter-details"
ENDPOINT_CURRENT_CONSUMPTION: Final = "/smartmeter/current-consumption"
ENDPOINT_YOUR_USAGE: Final = "/smartmeter/your-usage"
ENDPOINT_DAILY_CONSUMPTION: Final = "/smartmeter/daily-consumption"
ENDPOINT_YEARLY_CONSUMPTION: Final = "/smartmeter/yearly-consumption"

# Account / customer endpoints under API_BASE_URL. These all sit
# under `/api/account/...` (verified empirically on 2026-05-07).
ENDPOINT_CUSTOMER_DETAIL: Final = "/account/customer/detail"
ENDPOINT_PROPERTIES: Final = "/account/properties"
ENDPOINT_PROPERTIES_DETAIL: Final = "/account/properties/detail"

# Defaults.
DEFAULT_REQUEST_TIMEOUT_SECONDS: Final = 30.0
DEFAULT_USER_AGENT: Final = "pyyorkshirewater/0.2 (+https://github.com/dan-simms1/pyyorkshirewater)"

# How many seconds before access token expiry we refresh proactively.
#
# Yorkshire Water's IdP issues 15-minute (900s) access tokens. We set
# the leeway to 1000s (longer than the token lifetime itself) so that
# *every* call to ensure_token() triggers a silent renewal. That keeps
# the IdP session cookie touched at the integration's polling cadence,
# which is the cookie keepalive mechanism the SPA also relies on.
#
# Empirically: YW's session has a ~30 minute cliff. The SPA's
# oidc-client-ts setup silent renews every 14 minutes (60s before token
# expiry, default). To match or beat that cadence, the integration
# should poll at <= 14 minutes. The default in the HA integration is
# 10 minutes for headroom.
TOKEN_REFRESH_LEEWAY_SECONDS: Final = 1000.0

# PKCE code_verifier length. RFC 7636 allows 43-128. We use 64 for a 86 char
# url-safe base64 result well inside the allowed range.
PKCE_VERIFIER_BYTES: Final = 64

# Unit constants exposed by the customer UI. ASSUMPTION: query parameter name
# is "unit" with values "litres" or "m3". Confirm once a meter is live.
UNIT_LITRES: Final = "litres"
UNIT_CUBIC_METRES: Final = "m3"
