class BeaconIamError(Exception):
    code: str = "iam_error"


class AuthenticationError(BeaconIamError):
    code = "authentication_failed"


class AuthorizationError(BeaconIamError):
    code = "not_authorized"


class NotFoundError(BeaconIamError):
    code = "not_found"


class ConflictError(BeaconIamError):
    code = "conflict"
