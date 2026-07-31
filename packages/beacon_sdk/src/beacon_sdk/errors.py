"""beacon_sdk exception hierarchy."""

from __future__ import annotations


class BeaconSdkError(Exception):
    """Base SDK error."""

    code: str = "sdk_error"


class BeaconAuthError(BeaconSdkError):
    code = "auth_failed"


class BeaconTransportError(BeaconSdkError):
    code = "transport_failed"


class BeaconValidationError(BeaconSdkError):
    code = "validation_failed"


class BeaconQueueFullError(BeaconSdkError):
    """Raised only when the caller opts into queue-full exceptions."""

    code = "queue_full"
