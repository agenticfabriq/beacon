"""Errors raised by beacon_ablation."""


class BeaconAblationError(Exception):
    code: str = "ablation_error"


class InvalidConfigurationError(BeaconAblationError):
    code = "invalid_configuration"


class InsufficientDataError(BeaconAblationError):
    """Raised when a sweep cannot compute attribution from available data."""

    code = "insufficient_data"


class LayerNotDeclaredError(BeaconAblationError):
    """Raised when a config references a layer the SUT did not declare."""

    code = "layer_not_declared"
