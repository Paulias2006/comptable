"""Private Cloudinary storage for accounting attachments."""

from pathlib import PurePosixPath
from time import time

import cloudinary.uploader
import requests
from cloudinary.utils import private_download_url
from django.core.files.base import ContentFile
from django.core.files.storage import Storage
from django.utils.deconstruct import deconstructible


@deconstructible
class CloudinaryPrivateStorage(Storage):
    """Store scanned receipts as authenticated assets and proxy signed reads."""

    resource_type = "image"  # Cloudinary treats PDFs and images as image assets.
    delivery_type = "authenticated"

    class _RemoteFile:
        def __init__(self, response):
            self.response = response
            self.stream = response.raw
            self.stream.decode_content = True

        def read(self, size=-1):
            return self.stream.read(size)

        def close(self):
            self.response.close()

    def _save(self, name, content):
        original_path = PurePosixPath(name)
        extension = original_path.suffix.lower().lstrip(".")
        public_id = str(original_path.with_suffix(""))
        result = cloudinary.uploader.upload(
            content,
            public_id=public_id,
            resource_type=self.resource_type,
            type=self.delivery_type,
            overwrite=False,
            unique_filename=False,
            use_filename=False,
            secure=True,
        )
        return f"{result['public_id']}.{result.get('format') or extension}"

    def open_stream(self, name):
        path = PurePosixPath(name)
        public_id = str(path.with_suffix(""))
        file_format = path.suffix.lstrip(".")
        if not public_id or not file_format:
            raise OSError("Référence Cloudinary invalide.")
        signed_url = private_download_url(
            public_id,
            file_format,
            resource_type=self.resource_type,
            type=self.delivery_type,
            expires_at=int(time()) + 120,
        )
        response = requests.get(signed_url, timeout=(10, 120), stream=True)
        if response.status_code == 404:
            response.close()
            raise FileNotFoundError(name)
        try:
            response.raise_for_status()
        except requests.RequestException:
            response.close()
            raise
        return self._RemoteFile(response)

    def _open(self, name, mode="rb"):
        remote = self.open_stream(name)
        try:
            return ContentFile(remote.read(), name=name)
        finally:
            remote.close()

    def delete(self, name):
        path = PurePosixPath(name)
        result = cloudinary.uploader.destroy(
            str(path.with_suffix("")),
            resource_type=self.resource_type,
            type=self.delivery_type,
            invalidate=True,
        )
        return result.get("result") == "ok"

    def exists(self, name):
        # Upload names are generated with UUIDs, so a collision is not expected.
        return False

    def url(self, name):
        raise ValueError("Les justificatifs doivent être téléchargés via la route authentifiée de l’application.")

    def path(self, name):
        raise NotImplementedError("Les justificatifs Cloudinary n’ont pas de chemin local.")
