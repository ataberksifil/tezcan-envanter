from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage


class PrivateFileSystemStorage(FileSystemStorage):
    def url(self, name):
        raise ValueError("Private storage objects are not available via public URL.")


def get_private_storage() -> PrivateFileSystemStorage:
    return PrivateFileSystemStorage(
        location=str(settings.PRIVATE_MEDIA_ROOT),
        base_url="",
    )


def persist_private_file(storage_key: str, content: bytes) -> str:
    return get_private_storage().save(storage_key, ContentFile(content))


def open_private_file(storage_key: str, mode: str = "rb"):
    return get_private_storage().open(storage_key, mode)


def private_file_exists(storage_key: str) -> bool:
    return get_private_storage().exists(storage_key)
