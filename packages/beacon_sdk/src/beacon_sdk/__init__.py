"""Beacon SDK: async-first production-trace client."""

from beacon_sdk.client import BeaconClient
from beacon_sdk.errors import (
    BeaconAuthError,
    BeaconQueueFullError,
    BeaconSdkError,
    BeaconTransportError,
    BeaconValidationError,
)
from beacon_sdk.sync_wrapper import BeaconSyncClient

__all__ = [
    "BeaconAuthError",
    "BeaconClient",
    "BeaconQueueFullError",
    "BeaconSdkError",
    "BeaconSyncClient",
    "BeaconTransportError",
    "BeaconValidationError",
]
