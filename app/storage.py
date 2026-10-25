import os
import re
import asyncio
from abc import ABC, abstractmethod
from typing import Tuple, Optional
from functools import lru_cache

from app.config import get_settings


SAFE_KEY_PATTERN = re.compile(r"^[a-zA-Z0-9_.\-]+(/[a-zA-Z0-9_.\-]+)*$")


def sanitize_storage_key(key: str) -> str:
    clean = key.strip().replace("\\", "/").lstrip("/")
    if not clean or ".." in clean.split("/") or not SAFE_KEY_PATTERN.match(clean):
        raise ValueError(f"Path traversal or invalid storage key: '{key}'")
    return clean


class StorageBackend(ABC):
    @abstractmethod
    async def save_file(self, storage_key: str, data: bytes, content_type: str) -> str:
        """Save data to storage under storage_key and return the stored key."""
        pass

    @abstractmethod
    async def get_file(self, storage_key: str) -> Tuple[bytes, str]:
        """Retrieve data and content_type from storage. Raise FileNotFoundError if not found."""
        pass

    @abstractmethod
    async def delete_file(self, storage_key: str) -> bool:
        """Delete file from storage. Return True if deleted or did not exist."""
        pass

    @abstractmethod
    async def check_health(self) -> bool:
        """Check if storage backend is accessible."""
        pass


class LocalStorageBackend(StorageBackend):
    def __init__(self, base_dir: str):
        self.base_dir = os.path.abspath(base_dir)
        os.makedirs(self.base_dir, exist_ok=True)

    def _resolve_path(self, storage_key: str) -> str:
        # If a legacy absolute path within base_dir is supplied, handle gracefully
        if os.path.isabs(storage_key):
            clean_path = os.path.abspath(storage_key)
            if os.path.commonpath([self.base_dir, clean_path]) != self.base_dir:
                raise ValueError(f"Path traversal or invalid storage key: '{storage_key}'")
            return clean_path

        clean_key = sanitize_storage_key(storage_key)
        target = os.path.abspath(os.path.join(self.base_dir, clean_key))
        if os.path.commonpath([self.base_dir, target]) != self.base_dir:
            raise ValueError(f"Path traversal or invalid storage key: '{storage_key}'")
        return target

    async def save_file(self, storage_key: str, data: bytes, content_type: str) -> str:
        path = self._resolve_path(storage_key)
        os.makedirs(os.path.dirname(path), exist_ok=True)

        def _write():
            with open(path, "wb") as f:
                f.write(data)

        await asyncio.to_thread(_write)
        return sanitize_storage_key(storage_key) if not os.path.isabs(storage_key) else os.path.relpath(path, self.base_dir)

    async def get_file(self, storage_key: str) -> Tuple[bytes, str]:
        path = self._resolve_path(storage_key)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Storage object not found: {storage_key}")

        def _read() -> bytes:
            with open(path, "rb") as f:
                return f.read()

        data = await asyncio.to_thread(_read)
        # Determine basic mime from extension if available
        ext = os.path.splitext(path)[1].lower()
        mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
        content_type = mime_map.get(ext, "application/octet-stream")
        return data, content_type

    async def delete_file(self, storage_key: str) -> bool:
        try:
            path = self._resolve_path(storage_key)
            if os.path.isfile(path):
                await asyncio.to_thread(os.remove, path)
            return True
        except (ValueError, FileNotFoundError):
            return True

    async def check_health(self) -> bool:
        return os.path.isdir(self.base_dir) and os.access(self.base_dir, os.W_OK)


class S3StorageBackend(StorageBackend):
    def __init__(
        self,
        bucket_name: str,
        endpoint_url: Optional[str] = None,
        access_key_id: Optional[str] = None,
        secret_access_key: Optional[str] = None,
        region: str = "us-east-1",
        s3_client=None,
    ):
        self.bucket_name = bucket_name
        self.region = region
        if s3_client is not None:
            self._client = s3_client
        else:
            import boto3
            from botocore.config import Config
            config = Config(signature_version="s3v4", s3={"addressing_style": "path"})
            self._client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key,
                region_name=region,
                config=config,
            )

    async def save_file(self, storage_key: str, data: bytes, content_type: str) -> str:
        clean_key = sanitize_storage_key(storage_key)
        from botocore.exceptions import ClientError
        try:
            await asyncio.to_thread(
                self._client.put_object,
                Bucket=self.bucket_name,
                Key=clean_key,
                Body=data,
                ContentType=content_type,
            )
            return clean_key
        except ClientError as exc:
            raise RuntimeError(f"S3 storage put error: {exc}") from exc

    async def get_file(self, storage_key: str) -> Tuple[bytes, str]:
        clean_key = sanitize_storage_key(storage_key)
        from botocore.exceptions import ClientError
        try:
            response = await asyncio.to_thread(
                self._client.get_object,
                Bucket=self.bucket_name,
                Key=clean_key,
            )
            data = await asyncio.to_thread(response["Body"].read)
            content_type = response.get("ContentType", "application/octet-stream")
            return data, content_type
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code in ("NoSuchKey", "404"):
                raise FileNotFoundError(f"S3 object not found: {clean_key}") from exc
            raise RuntimeError(f"S3 storage get error: {exc}") from exc

    async def delete_file(self, storage_key: str) -> bool:
        try:
            clean_key = sanitize_storage_key(storage_key)
            await asyncio.to_thread(
                self._client.delete_object,
                Bucket=self.bucket_name,
                Key=clean_key,
            )
            return True
        except Exception:
            return False

    async def check_health(self) -> bool:
        from botocore.exceptions import ClientError
        try:
            await asyncio.to_thread(
                self._client.head_bucket,
                Bucket=self.bucket_name,
            )
            return True
        except ClientError:
            return False


@lru_cache
def get_storage_backend() -> StorageBackend:
    settings = get_settings()
    backend_type = settings.STORAGE_BACKEND.lower().strip()
    if backend_type in ("s3", "supabase"):
        return S3StorageBackend(
            bucket_name=settings.S3_BUCKET_NAME,
            endpoint_url=settings.S3_ENDPOINT_URL,
            access_key_id=settings.S3_ACCESS_KEY_ID,
            secret_access_key=settings.S3_SECRET_ACCESS_KEY,
            region=settings.S3_REGION,
        )
    return LocalStorageBackend(base_dir=settings.MEDIA_STORAGE_DIR)
