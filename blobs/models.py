import logging
import uuid
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import models
from django.utils import timezone
from PIL import Image, ImageOps, UnidentifiedImageError

from . import preview as preview_fetch

log = logging.getLogger(__name__)


def _upload_to(instance, filename):
    """Random name under a dated directory.

    The uploaded filename never reaches the filesystem: it is user controlled,
    and one flat directory gets unusable past a few thousand entries.
    """
    suffix = Path(filename).suffix.lower()[:10]
    # A callable upload_to gets no strftime expansion, so the date is built here.
    return f"blobs/{timezone.now():%Y/%m}/{uuid.uuid4().hex}{suffix}"


class Blob(models.Model):
    # A blob hanging off another blob: a follow-up, a second thought, the
    # screenshot you meant to attach. Only top-level blobs show in the feed.
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        related_name="children",
        on_delete=models.CASCADE,
    )
    text = models.TextField(blank=True)
    url = models.URLField(max_length=2000, blank=True)

    # Link metadata, resolved once at creation time.
    title = models.CharField(max_length=300, blank=True)
    description = models.TextField(blank=True)
    site_name = models.CharField(max_length=120, blank=True)
    preview_image = models.ImageField(upload_to=_upload_to, blank=True, editable=False)
    embed_url = models.URLField(max_length=2000, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    # Set by the edit view. Creating a blob writes the row two or three times
    # already, so timestamps cannot tell an edit from a slow link fetch.
    edited = models.BooleanField(default=False, editable=False)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.kind}:{self.pk}"

    @property
    def kind(self):
        """Derived, never stored: images arrive after the row exists."""
        if self.images.exists():
            return "image"
        return "link" if self.url else "text"

    @property
    def is_video(self):
        return bool(self.embed_url)

    def clear_link_meta(self):
        """Drop everything derived from the old URL, file included."""
        if self.preview_image:
            self.preview_image.storage.delete(self.preview_image.name)
            self.preview_image = ""
        self.title = ""
        self.description = ""
        self.site_name = ""
        self.embed_url = ""

    def fetch_preview(self):
        """Populate link metadata. Never raises: a bare link is still a blob."""
        if not self.url:
            return
        result = preview_fetch.fetch(self.url)
        self.title = result.title[:300]
        self.description = result.description[:2000]
        self.site_name = result.site_name[:120]
        self.embed_url = result.embed_url
        if result.image_bytes:
            self.preview_image.save(
                f"{uuid.uuid4().hex}{Path(result.image_name).suffix.lower()[:10] or '.jpg'}",
                ContentFile(result.image_bytes),
                save=False,
            )

    def delete(self, *args, **kwargs):
        # A cascading delete drops the rows without ever calling this method, so
        # sub-blobs and their files are cleaned up explicitly first. Django
        # would otherwise leave every attachment orphaned in the volume.
        for child in self.children.all():
            child.delete()
        for image in self.images.all():
            image.delete()
        preview = self.preview_image
        super().delete(*args, **kwargs)
        if preview:
            preview.storage.delete(preview.name)


class BlobImage(models.Model):
    """One picture on a blob. A blob can carry as many as you drop on it."""

    blob = models.ForeignKey(Blob, related_name="images", on_delete=models.CASCADE)
    image = models.ImageField(upload_to=_upload_to)
    # Downscaled, EXIF-corrected copy. Cards render this; the original is only
    # served from the blob's own page.
    thumb = models.ImageField(upload_to=_upload_to, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Attachment order is the order they were added, not newest first.
        ordering = ["id"]

    def __str__(self):
        return self.image.name

    def build_thumb(self):
        """Normalise the upload: rotate, downscale, re-encode to WebP.

        Phone cameras store orientation as an EXIF tag rather than rotating
        pixels, so without exif_transpose every portrait photo renders on its
        side. Downscaling also keeps a 48 MP original out of the feed.
        """
        try:
            with Image.open(self.image.path) as source:
                image = ImageOps.exif_transpose(source)
                image.thumbnail(
                    (settings.BLOB_THUMB_MAX_PX, settings.BLOB_THUMB_MAX_PX),
                    Image.LANCZOS,
                )
                # WebP carries alpha, so transparent PNGs do not go black.
                mode = "RGBA" if image.mode in ("RGBA", "LA", "P") else "RGB"
                buffer = BytesIO()
                image.convert(mode).save(buffer, "WEBP", quality=82, method=4)
        except (UnidentifiedImageError, OSError) as exc:
            log.warning("thumbnail failed for image %s: %s", self.pk, exc)
            return
        self.thumb.save(
            f"{uuid.uuid4().hex}.webp", ContentFile(buffer.getvalue()), save=True
        )

    @property
    def display_url(self):
        return self.thumb.url if self.thumb else self.image.url

    def delete(self, *args, **kwargs):
        files = [self.image, self.thumb]
        super().delete(*args, **kwargs)
        for handle in files:
            if handle:
                handle.storage.delete(handle.name)
