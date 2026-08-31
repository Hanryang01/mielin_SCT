from __future__ import annotations

from .config import S3Settings

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except ModuleNotFoundError:  # pragma: no cover - depends on runtime environment
    boto3 = None
    BotoCoreError = ClientError = Exception


class S3ImageClient:
    """S3 이미지 프리사인드 URL 발급. 자격증명이 없으면 조용히 비활성화된다."""

    def __init__(self, settings: S3Settings) -> None:
        self.settings = settings
        self._client = None
        if settings.configured and boto3 is not None:
            self._client = boto3.client(
                "s3",
                aws_access_key_id=settings.aws_access_key_id,
                aws_secret_access_key=settings.aws_secret_access_key,
                region_name=settings.aws_region,
            )

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def presign(self, s3_key: str) -> str | None:
        if not self._client or not s3_key:
            return None
        try:
            return self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.settings.bucket, "Key": s3_key},
                ExpiresIn=self.settings.presign_expires_seconds,
            )
        except (BotoCoreError, ClientError):
            return None
