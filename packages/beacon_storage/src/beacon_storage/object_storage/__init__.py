from beacon_storage.object_storage.adapter import ObjectStorage
from beacon_storage.object_storage.local import LocalFsStorage
from beacon_storage.object_storage.s3 import S3Storage

__all__ = ["LocalFsStorage", "ObjectStorage", "S3Storage"]
