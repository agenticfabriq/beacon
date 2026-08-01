"""Beacon runner exception hierarchy."""


class BeaconRunnerError(Exception):
    code: str = "runner_error"


class SutNotFoundError(BeaconRunnerError):
    code = "sut_not_found"


class SutValidationError(BeaconRunnerError):
    code = "sut_validation_failed"


class SutInvocationError(BeaconRunnerError):
    code = "sut_invocation_failed"


class HarnessModeNotSupportedError(BeaconRunnerError):
    code = "mode_not_supported"


class SutTimeoutError(BeaconRunnerError):
    code = "sut_timeout"


class SutIdentityMismatchError(BeaconRunnerError):
    code = "sut_identity_mismatch"
