from __future__ import annotations

import io
from typing import TYPE_CHECKING, cast

import boto3
from botocore.client import Config

if TYPE_CHECKING:
    from typing import BinaryIO


class S3Storage:
    """Amazon S3 ObjectStorage. Requires boto3 credentials in the environment."""

    def __init__(
        self, *, bucket: str, region: str | None = None, endpoint_url: str | None = None
    ) -> None:
        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            region_name=region,
            endpoint_url=endpoint_url,
            config=Config(signature_version="s3v4"),
        )

    def put(self, key: str, stream: BinaryIO) -> None:
        """Upload ``stream`` to S3 at ``key`` in the configured bucket."""
        self.client.upload_fileobj(stream, self.bucket, key)

    def get(self, key: str) -> BinaryIO:
        """Return an in-memory stream containing the object stored at ``key``."""
        buf = io.BytesIO()
        self.client.download_fileobj(self.bucket, key, buf)
        buf.seek(0)
        return buf

    def exists(self, key: str) -> bool:
        """Return whether an object exists at ``key`` via a HEAD request."""
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except self.client.exceptions.ClientError:
            return False

    def delete(self, key: str) -> None:
        """Delete the object stored at ``key`` from S3."""
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def signed_url(self, key: str, *, expires_seconds: int) -> str:
        """Return a presigned GET URL for ``key`` valid for ``expires_seconds``."""
        url = self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_seconds,
        )
        return cast("str", url)
