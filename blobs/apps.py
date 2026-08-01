from django.apps import AppConfig


class BlobsConfig(AppConfig):
    name = "blobs"

    def ready(self):
        # Teaches Pillow to open HEIC/HEIF, so iPhone photos upload like any
        # other image instead of failing ImageField validation.
        from pillow_heif import register_heif_opener

        register_heif_opener()
