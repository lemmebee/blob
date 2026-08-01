import re

from django import forms
from django.conf import settings
from django.core.validators import URLValidator

from .models import Blob

# A blob whose entire body is one URL is a link, not a sentence about a link.
URL_ONLY = re.compile(r"https?://\S+", re.IGNORECASE)

_validate_url = URLValidator(schemes=["http", "https"])


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """Django's FileField cleans one upload; this cleans the whole batch.

    There is no built-in multi-file field, only this documented recipe.
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        clean_one = super().clean
        if isinstance(data, (list, tuple)):
            return [clean_one(item, initial) for item in data]
        return [clean_one(data, initial)] if data else []


class BlobForm(forms.ModelForm):
    """One box takes everything: text, a pasted link, any number of images."""

    images = MultipleFileField(required=False)

    class Meta:
        model = Blob
        fields = ["text"]
        widgets = {
            "text": forms.Textarea(
                attrs={
                    "rows": 2,
                    "placeholder": "Feed the blob. It eats words, links and screenshots.",
                    "aria-label": "New blob",
                }
            ),
        }

    def __init__(self, *args, placeholder=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["images"].widget.attrs.update(
            {"class": "file-input", "accept": "image/*", "multiple": True}
        )
        if placeholder:
            self.fields["text"].widget.attrs["placeholder"] = placeholder

    def clean_text(self):
        text = self.cleaned_data["text"]
        # Unlimited on screen, not on disk: one runaway paste should not eat
        # the volume.
        if len(text) > settings.BLOB_MAX_TEXT_CHARS:
            raise forms.ValidationError(
                f"Text is {len(text):,} characters, limit is "
                f"{settings.BLOB_MAX_TEXT_CHARS:,}."
            )
        return text

    def clean_images(self):
        # No cap on how many: only on how big any single one is.
        for image in self.cleaned_data["images"]:
            if image.size > settings.BLOB_MAX_IMAGE_BYTES:
                limit_mb = settings.BLOB_MAX_IMAGE_BYTES / 1024 / 1024
                raise forms.ValidationError(
                    f"{image.name} is over {limit_mb:.0f} MB."
                )
        return self.cleaned_data["images"]

    def retained_images(self):
        """Images the blob already carries. Nothing is retained on a new blob."""
        return 0

    def clean(self):
        cleaned = super().clean()
        text = (cleaned.get("text") or "").strip()
        images = cleaned.get("images") or []

        if not text and not images and not self.retained_images():
            raise forms.ValidationError("Nothing to blob yet.")

        url = ""
        if text and URL_ONLY.fullmatch(text):
            try:
                _validate_url(text)
                url = text[:2000]
            except forms.ValidationError:
                url = ""
        # instance survives _post_clean, which only writes the Meta fields.
        self.instance.url = url
        cleaned["text"] = "" if url else text
        return cleaned


class BlobEditForm(BlobForm):
    """Same box, editing what is already there.

    Existing attachments are ticked for removal with plain checkboxes, so the
    edit page works with JavaScript off.
    """

    def removed_ids(self):
        return {
            int(value)
            for value in self.data.getlist("remove_images")
            if value.isdigit()
        }

    def retained_images(self):
        return self.instance.images.exclude(pk__in=self.removed_ids()).count()
