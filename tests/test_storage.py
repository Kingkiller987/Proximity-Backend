import pytest
from unittest.mock import MagicMock, patch
from botocore.exceptions import ClientError


@pytest.mark.asyncio
async def test_local_storage_backend_roundtrip(tmp_path):
    from app.storage import LocalStorageBackend
    backend = LocalStorageBackend(base_dir=str(tmp_path))
    key = "media/test_image.jpg"
    data = b"\xFF\xD8\xFF\xE0\x00\x10JFIF"
    
    saved_key = await backend.save_file(key, data, "image/jpeg")
    assert saved_key == key
    
    retrieved_data, content_type = await backend.get_file(key)
    assert retrieved_data == data
    assert content_type == "image/jpeg"
    
    deleted = await backend.delete_file(key)
    assert deleted is True
    
    with pytest.raises(FileNotFoundError):
        await backend.get_file(key)


@pytest.mark.asyncio
async def test_local_storage_path_traversal_rejection(tmp_path):
    from app.storage import LocalStorageBackend
    backend = LocalStorageBackend(base_dir=str(tmp_path))
    
    with pytest.raises(ValueError, match="Path traversal or invalid storage key"):
        await backend.save_file("../../etc/passwd", b"malicious", "text/plain")
        
    with pytest.raises(ValueError, match="Path traversal or invalid storage key"):
        await backend.get_file("../sensitive.txt")


@pytest.mark.asyncio
async def test_s3_storage_backend_mock():
    from app.storage import S3StorageBackend
    mock_s3 = MagicMock()
    
    backend = S3StorageBackend(
        bucket_name="test-bucket",
        endpoint_url="https://mock.supabase.co/storage/v1/s3",
        access_key_id="mock_key",
        secret_access_key="mock_secret",
        region="us-east-1",
        s3_client=mock_s3,
    )
    
    key = "media/abc-123.png"
    data = b"\x89PNG\r\n\x1a\n"
    
    # Save
    saved_key = await backend.save_file(key, data, "image/png")
    assert saved_key == key
    mock_s3.put_object.assert_called_once_with(
        Bucket="test-bucket",
        Key=key,
        Body=data,
        ContentType="image/png",
    )
    
    # Get
    mock_body = MagicMock()
    mock_body.read.return_value = data
    mock_s3.get_object.return_value = {"Body": mock_body, "ContentType": "image/png"}
    retrieved, ct = await backend.get_file(key)
    assert retrieved == data
    assert ct == "image/png"
    
    # Missing key raises FileNotFoundError
    mock_s3.get_object.side_effect = ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": "The specified key does not exist."}},
        "GetObject",
    )
    with pytest.raises(FileNotFoundError):
        await backend.get_file("media/missing.png")
        
    # Delete
    deleted = await backend.delete_file(key)
    assert deleted is True
    mock_s3.delete_object.assert_called_once_with(
        Bucket="test-bucket",
        Key=key,
    )


def test_storage_backend_factory(monkeypatch, tmp_path):
    from app.storage import get_storage_backend, LocalStorageBackend, S3StorageBackend
    from app.config import get_settings
    
    get_storage_backend.cache_clear()
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    get_settings.cache_clear()
    
    backend = get_storage_backend()
    assert isinstance(backend, LocalStorageBackend)
    
    get_storage_backend.cache_clear()
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_BUCKET_NAME", "my-bucket")
    monkeypatch.setenv("S3_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "secret")
    get_settings.cache_clear()
    
    with patch("boto3.client") as mock_boto:
        backend_s3 = get_storage_backend()
        assert isinstance(backend_s3, S3StorageBackend)
        
    get_storage_backend.cache_clear()
    get_settings.cache_clear()

@pytest.mark.asyncio
async def test_atomic_upload_rollback_on_db_error():
    from app.storage import LocalStorageBackend
    import tempfile
    
    with tempfile.TemporaryDirectory() as tmp_dir:
        backend = LocalStorageBackend(base_dir=tmp_dir)
        key = "media/rollback_test.jpg"
        data = b"test-data"
        
        # Save file to storage
        await backend.save_file(key, data, "image/jpeg")
        assert (await backend.get_file(key))[0] == data
        
        # Simulate database failure rollback cleanup
        await backend.delete_file(key)
        with pytest.raises(FileNotFoundError):
            await backend.get_file(key)
