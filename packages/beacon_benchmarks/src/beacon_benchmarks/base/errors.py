"""Exceptions surfaced by benchmark adapter machinery."""


class BeaconBenchmarkError(Exception):
    """Base class for benchmark adapter errors."""


class AdapterNotRegisteredError(BeaconBenchmarkError):
    """Looked up an adapter name that was not registered."""


class DownloadFailedError(BeaconBenchmarkError):
    """Public source and internal mirror both failed."""


class ShaMismatchError(BeaconBenchmarkError):
    """Downloaded artifact's SHA-256 did not match the expected value."""


class LargeAssetSkippedError(BeaconBenchmarkError):
    """Asset is large and include-large was not set."""


class IngestError(BeaconBenchmarkError):
    """Benchmark ingest failed."""


class SqlTranslationError(BeaconBenchmarkError):
    """A SQLite query could not be translated to Postgres."""
