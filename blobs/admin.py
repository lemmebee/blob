from django.contrib import admin

from .models import Blob, BlobImage


class BlobImageInline(admin.TabularInline):
    model = BlobImage
    extra = 0
    readonly_fields = ("thumb", "created_at")


@admin.register(Blob)
class BlobAdmin(admin.ModelAdmin):
    list_display = ("id", "kind", "summary", "site_name", "parent", "created_at")
    list_filter = ("created_at",)
    search_fields = ("text", "url", "title", "description")
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "preview_image")
    inlines = [BlobImageInline]

    @admin.display(description="Preview")
    def summary(self, blob):
        return (blob.title or blob.text or blob.url)[:80]

    def delete_queryset(self, request, queryset):
        # Bulk delete skips Model.delete(), which is where sub-blobs and files
        # are cleaned up.
        for blob in queryset:
            blob.delete()
