from django.conf import settings
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Prefetch
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .forms import BlobEditForm, BlobForm
from .models import Blob, BlobImage


def _create(request, parent=None):
    """Shared by the feed and a blob's own page. Returns the blob, or None."""
    form = BlobForm(request.POST, request.FILES)
    if not form.is_valid():
        return form, None

    # All or nothing: a failure partway through the attachments would otherwise
    # leave a blob in the feed carrying half of what was dropped on it.
    with transaction.atomic():
        blob = form.save(commit=False)
        blob.parent = parent
        blob.save()
        for upload in form.cleaned_data["images"]:
            BlobImage.objects.create(blob=blob, image=upload).build_thumb()
    # Link metadata needs the row to exist and is best effort. It stays outside
    # the transaction: it makes a network call, and SQLite has one writer.
    blob.fetch_preview()
    blob.save()
    return form, blob


def feed(request):
    if request.method == "POST":
        form, blob = _create(request)
        if blob:
            # Post/redirect/get, so a refresh does not repost the blob.
            return HttpResponseRedirect(f"{reverse('blobs:feed')}#blob-{blob.pk}")
    else:
        form = BlobForm()

    blobs = (
        Blob.objects.filter(parent__isnull=True)
        .prefetch_related("images")
        .annotate(child_count=Count("children"))
        # annotate() adds a GROUP BY and drops Meta.ordering, which leaves the
        # paginator with an unordered queryset.
        .order_by("-created_at", "-id")
    )
    page = Paginator(blobs, settings.BLOB_PAGE_SIZE).get_page(request.GET.get("page"))
    # The infinite-scroll sentinel asks for the next page and appends it, so
    # only the card markup comes back.
    template = "blobs/_page.html" if request.headers.get("X-Partial") else "blobs/feed.html"
    return render(request, template, {"form": form, "page": page})


def detail(request, pk):
    blob = get_object_or_404(
        Blob.objects.prefetch_related("images", "children__images"), pk=pk
    )

    if request.method == "POST":
        form, child = _create(request, parent=blob)
        if child:
            return HttpResponseRedirect(
                f"{reverse('blobs:detail', args=[blob.pk])}#blob-{child.pk}"
            )
    else:
        form = BlobForm()

    form.fields["text"].widget.attrs["placeholder"] = "The blob is still hungry."
    return render(
        request,
        "blobs/detail.html",
        {"blob": blob, "form": form, "children": blob.children.order_by("created_at", "id")},
    )


def edit(request, pk):
    blob = get_object_or_404(Blob.objects.prefetch_related("images"), pk=pk)
    # clean() rewrites instance.url, so the old value is read before binding.
    was = blob.url

    if request.method == "POST":
        form = BlobEditForm(request.POST, request.FILES, instance=blob)
        if form.is_valid():
            with transaction.atomic():
                blob = form.save(commit=False)
                blob.edited = True
                blob.save()
                for image in blob.images.filter(pk__in=form.removed_ids()):
                    image.delete()
                for upload in form.cleaned_data["images"]:
                    BlobImage.objects.create(blob=blob, image=upload).build_thumb()
            if blob.url != was:
                # The link changed or went away; nothing derived from the old
                # one is true any more.
                blob.clear_link_meta()
                blob.fetch_preview()
                blob.save()
            return redirect("blobs:detail", pk=blob.pk)
    else:
        # A link blob keeps its URL in the box it was typed into.
        form = BlobEditForm(instance=blob, initial={"text": blob.text or blob.url})

    # The shared widget announces itself as "New blob"; this box is not that.
    form.fields["text"].widget.attrs["aria-label"] = "Blob contents"
    return render(request, "blobs/edit.html", {"blob": blob, "form": form})


@require_POST
def delete(request, pk):
    blob = get_object_or_404(Blob, pk=pk)
    parent_id = blob.parent_id
    blob.delete()
    # Deleting a sub-blob keeps you on the blob you were reading.
    if parent_id:
        return redirect("blobs:detail", pk=parent_id)
    return redirect("blobs:feed")
