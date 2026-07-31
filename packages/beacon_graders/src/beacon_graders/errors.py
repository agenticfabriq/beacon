"""Grader exception hierarchy."""


class BeaconGraderError(Exception):
    code: str = "grader_error"


class GraderTimeoutError(BeaconGraderError):
    code = "grader_timeout"


class GraderJudgeError(BeaconGraderError):
    code = "grader_judge_failed"


class GraderConfigError(BeaconGraderError):
    code = "grader_config_invalid"
