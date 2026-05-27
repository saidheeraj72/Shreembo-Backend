"""Supabase Storage client."""
import uuid
from typing import Optional, BinaryIO
import logging
from src.config import settings
from src.core.database import db

logger = logging.getLogger(__name__)

BUCKET = settings.SUPABASE_STORAGE_BUCKET


class StorageClient:
    """Wrapper around Supabase Storage API."""

    @property
    def storage(self):
        return db.admin.storage

    @property
    def bucket(self):
        return self.storage.from_(BUCKET)

    def generate_key(self, org_id: str, filename: str) -> str:
        unique_id = str(uuid.uuid4())[:8]
        safe_name = "".join(c if c.isalnum() or c in ".-_" else "_" for c in filename)
        return f"orgs/{org_id}/files/{unique_id}_{safe_name}"

    async def upload_file(self, file_obj: BinaryIO, key: str, content_type: str) -> dict:
        data = file_obj.read()
        self.bucket.upload(key, data, file_options={"content-type": content_type, "upsert": "true"})
        return {'bucket': BUCKET, 'key': key}

    async def upload_file_bytes(self, file_bytes: bytes, key: str, content_type: str) -> dict:
        self.bucket.upload(key, file_bytes, file_options={"content-type": content_type, "upsert": "true"})
        return {'bucket': BUCKET, 'key': key}

    async def get_signed_download_url(self, key: str, expires: int = 3600) -> str:
        result = self.bucket.create_signed_url(key, expires)
        return result["signedURL"]

    async def get_signed_view_url(self, key: str, expires: int = 3600) -> str:
        result = self.bucket.create_signed_url(key, expires)
        return result["signedURL"]

    async def get_public_url(self, key: str) -> str:
        result = self.bucket.get_public_url(key)
        return result

    async def delete_file(self, key: str) -> bool:
        try:
            self.bucket.remove([key])
            return True
        except Exception as e:
            logger.error("Storage delete error: %s", e)
            return False

    async def get_file_content(self, key: str) -> Optional[bytes]:
        try:
            return self.bucket.download(key)
        except Exception as e:
            logger.error("Storage download error: %s", e)
            return None

    async def copy_file(self, source_key: str, dest_key: str) -> bool:
        try:
            self.bucket.copy(source_key, dest_key)
            return True
        except Exception as e:
            logger.error("Storage copy error: %s", e)
            return False


s3_client = StorageClient()
