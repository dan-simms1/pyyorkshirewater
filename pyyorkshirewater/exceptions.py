"""Exception types raised by pyyorkshirewater.

The hierarchy is intentionally small so that callers can catch broad classes
of failure without losing the ability to distinguish auth errors from API
errors. Every exception inherits from `YorkshireWaterError`.
"""

from __future__ import annotations


class YorkshireWaterError(Exception):
    """Base class for every error raised by this library."""


class YorkshireWaterAuthError(YorkshireWaterError):
    """Authentication failed against the IdentityServer.

    Raised for invalid credentials, an expired or revoked refresh token, or a
    missing client secret. The original IdentityServer error code is exposed
    on the `error` attribute when one is available.
    """

    def __init__(
        self,
        message: str,
        *,
        error: str | None = None,
        error_description: str | None = None,
        status_code: int | None = None,
    ) -> None:
        """Initialise the error with optional OAuth metadata."""
        super().__init__(message)
        self.error = error
        self.error_description = error_description
        self.status_code = status_code


class YorkshireWaterAPIError(YorkshireWaterError):
    """A non-auth HTTP error from the customer self-service API.

    The HTTP status code is exposed on the `status_code` attribute. The raw
    response body, if it could be read, is on `body`.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: str | None = None,
    ) -> None:
        """Initialise the error with the offending status code and body."""
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class YorkshireWaterMeterNotReadyError(YorkshireWaterError):
    """A consumption endpoint was called but the meter is not LIVE.

    Callers should check `YorkshireWaterClient.meter_status` before requesting
    consumption data. This exception is the safety net for code that did not.
    """


class YorkshireWaterRateLimitError(YorkshireWaterAPIError):
    """The API returned 429 Too Many Requests.

    The `retry_after` attribute holds the value of the `Retry-After` header
    in seconds when the server provided one. Defensive callers should respect
    it.
    """

    def __init__(
        self,
        message: str,
        *,
        retry_after: float | None = None,
        status_code: int | None = 429,
        body: str | None = None,
    ) -> None:
        """Initialise the error with retry metadata."""
        super().__init__(message, status_code=status_code, body=body)
        self.retry_after = retry_after
