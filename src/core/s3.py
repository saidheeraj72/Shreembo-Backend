"""AWS S3 client for document storage."""
import uuid
from typing import Optional, BinaryIO
import logging
import boto3
from botocore.config import Config
from src.config import settings

logger = logging.getLogger(__name__)


class S3Client:
    def __init__(self):
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client = boto3.client(
                's3',
                aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                region_name=settings.AWS_S3_REGION,
                endpoint_url=settings.AWS_S3_ENDPOINT_URL,
                config=Config(signature_version='s3v4')
            )
        return self._client

    def generate_key(self, org_id: str, filename: str) -> str:
        unique_id = str(uuid.uuid4())[:8]
        safe_name = "".join(c if c.isalnum() or c in ".-_" else "_" for c in filename)
        return f"orgs/{org_id}/files/{unique_id}_{safe_name}"

    async def upload_file(self, file_obj: BinaryIO, key: str, content_type: str) -> dict:
        self.client.upload_fileobj(
            file_obj, settings.AWS_S3_BUCKET, key,
            ExtraArgs={'ContentType': content_type, 'ServerSideEncryption': 'AES256'}
        )
        return {'bucket': settings.AWS_S3_BUCKET, 'key': key, 'region': settings.AWS_S3_REGION}

    async def get_presigned_upload_url(self, key: str, content_type: str, expires: int = 3600) -> dict:
        url = self.client.generate_presigned_url(
            'put_object',
            Params={'Bucket': settings.AWS_S3_BUCKET, 'Key': key, 'ContentType': content_type},
            ExpiresIn=expires
        )
        return {'upload_url': url, 'key': key}

    async def get_presigned_download_url(self, key: str, filename: str = None, expires: int = 3600) -> str:
        params = {'Bucket': settings.AWS_S3_BUCKET, 'Key': key}
        if filename:
            params['ResponseContentDisposition'] = f'attachment; filename="{filename}"'
        return self.client.generate_presigned_url('get_object', Params=params, ExpiresIn=expires)

    async def get_presigned_view_url(self, key: str, content_type: str = None, expires: int = 3600) -> str:
        """Get presigned URL for viewing (inline disposition)."""
        params = {'Bucket': settings.AWS_S3_BUCKET, 'Key': key}
        params['ResponseContentDisposition'] = 'inline'
        if content_type:
            params['ResponseContentType'] = content_type
        return self.client.generate_presigned_url('get_object', Params=params, ExpiresIn=expires)

    async def delete_file(self, key: str) -> bool:
        try:
            self.client.delete_object(Bucket=settings.AWS_S3_BUCKET, Key=key)
            return True
        except Exception:
            return False

    async def get_file_content(self, key: str) -> Optional[bytes]:
        try:
            response = self.client.get_object(Bucket=settings.AWS_S3_BUCKET, Key=key)
            return response['Body'].read()
        except Exception:
            return None

    async def copy_file(self, source_key: str, dest_key: str) -> bool:
        """Copy a file within S3 bucket."""
        try:
            copy_source = {'Bucket': settings.AWS_S3_BUCKET, 'Key': source_key}
            self.client.copy_object(
                CopySource=copy_source,
                Bucket=settings.AWS_S3_BUCKET,
                Key=dest_key,
                ServerSideEncryption='AES256'
            )
            return True
        except Exception as e:
            logger.error("S3 copy error: %s", e)
            return False

    async def upload_file_bytes(self, file_bytes: bytes, key: str, content_type: str) -> dict:
        """Upload file bytes directly to S3."""
        from io import BytesIO
        file_obj = BytesIO(file_bytes)
        self.client.upload_fileobj(
            file_obj, settings.AWS_S3_BUCKET, key,
            ExtraArgs={'ContentType': content_type, 'ServerSideEncryption': 'AES256'}
        )
        return {'bucket': settings.AWS_S3_BUCKET, 'key': key, 'region': settings.AWS_S3_REGION}


s3_client = S3Client()
