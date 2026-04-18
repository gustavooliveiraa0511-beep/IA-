"""
Upload pra Cloudflare R2 (S3-compatible).

10 GB grátis + saída de dados grátis = melhor opção pra vídeo.
"""
from __future__ import annotations

import mimetypes
from pathlib import Path

import boto3
from botocore.config import Config as BotoConfig
from tenacity import retry, stop_after_attempt, wait_exponential

from src.utils.config import config
from src.utils.logger import get_logger

logger = get_logger(__name__)


class R2Uploader:
    def __init__(self) -> None:
        if not all([
            config.r2_account_id,
            config.r2_access_key_id,
            config.r2_secret_access_key,
        ]):
            raise ValueError("Credenciais do Cloudflare R2 não configuradas")

        endpoint = f"https://{config.r2_account_id}.r2.cloudflarestorage.com"
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=config.r2_access_key_id,
            aws_secret_access_key=config.r2_secret_access_key,
            config=BotoConfig(signature_version="s3v4"),
            region_name="auto",
        )
        self.bucket = config.r2_bucket_name
        self.public_url = config.r2_public_url.rstrip("/") if config.r2_public_url else ""

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=10))
    def upload_file(self, local_path: Path, remote_key: str) -> str:
        content_type = mimetypes.guess_type(str(local_path))[0] or "application/octet-stream"
        size_mb = local_path.stat().st_size / (1024 * 1024)
        logger.info(f"R2: subindo {local_path.name} ({size_mb:.1f} MB) → {remote_key}")

        self.client.upload_file(
            str(local_path),
            self.bucket,
            remote_key,
            ExtraArgs={"ContentType": content_type},
        )

        if self.public_url:
            return f"{self.public_url}/{remote_key}"
        # Fallback: presigned URL válida por 7 dias
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": remote_key},
            ExpiresIn=7 * 24 * 3600,
        )
