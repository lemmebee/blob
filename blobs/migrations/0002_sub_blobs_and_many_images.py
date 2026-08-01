import blobs.models
import django.db.models.deletion
from django.db import migrations, models


def move_images(apps, schema_editor):
    """Carry each blob's single image over to the new attachment table.

    Runs before the old columns are dropped, so nothing has to be re-uploaded
    or re-thumbnailed.
    """
    Blob = apps.get_model("blobs", "Blob")
    BlobImage = apps.get_model("blobs", "BlobImage")
    BlobImage.objects.bulk_create(
        BlobImage(blob=blob, image=blob.image, thumb=blob.thumb)
        for blob in Blob.objects.exclude(image="")
    )


def restore_images(apps, schema_editor):
    """Reverse: put the first attachment back on the blob itself."""
    Blob = apps.get_model("blobs", "Blob")
    BlobImage = apps.get_model("blobs", "BlobImage")
    seen = set()
    # DISTINCT ON is Postgres only, and this app runs on SQLite.
    for image in BlobImage.objects.order_by("blob_id", "id"):
        if image.blob_id in seen:
            continue
        seen.add(image.blob_id)
        Blob.objects.filter(pk=image.blob_id).update(
            image=image.image, thumb=image.thumb, kind="image"
        )


class Migration(migrations.Migration):

    dependencies = [
        ("blobs", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="blob",
            name="parent",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="children",
                to="blobs.blob",
            ),
        ),
        migrations.CreateModel(
            name="BlobImage",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("image", models.ImageField(upload_to=blobs.models._upload_to)),
                (
                    "thumb",
                    models.ImageField(
                        blank=True, editable=False, upload_to=blobs.models._upload_to
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "blob",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="images",
                        to="blobs.blob",
                    ),
                ),
            ],
            options={"ordering": ["id"]},
        ),
        migrations.RunPython(move_images, restore_images),
        # Removing a column reverses into adding it back, which fails on a table
        # with rows unless the field carries a default. These three exist only
        # so a rollback to 0001 works.
        migrations.AlterField(
            model_name="blob",
            name="kind",
            field=models.CharField(
                choices=[("text", "Text"), ("image", "Image"), ("link", "Link")],
                default="text",
                editable=False,
                max_length=8,
            ),
        ),
        migrations.AlterField(
            model_name="blob",
            name="image",
            field=models.ImageField(
                blank=True, default="", upload_to=blobs.models._upload_to
            ),
        ),
        migrations.AlterField(
            model_name="blob",
            name="thumb",
            field=models.ImageField(
                blank=True, default="", editable=False, upload_to=blobs.models._upload_to
            ),
        ),
        migrations.RemoveField(model_name="blob", name="image"),
        migrations.RemoveField(model_name="blob", name="thumb"),
        migrations.RemoveField(model_name="blob", name="kind"),
    ]
