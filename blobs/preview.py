"""Link previews.

A URL is fetched exactly once, when the blob is created. Whatever we learn
(title, description, thumbnail, embeddable player) is copied into the row, so
rendering the feed never touches the network and a dead link still shows what
it used to be.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import socket
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlparse

import requests

log = logging.getLogger(__name__)

TIMEOUT = (3, 5)  # connect, read
MAX_HTML_BYTES = 512 * 1024  # <head> is always near the top; stop after this
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 4
USER_AGENT = "Mozilla/5.0 (compatible; blob/1.0; +link-preview)"

YOUTUBE_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|embed/|shorts/|live/)|youtu\.be/)([\w-]{11})"
)
VIMEO_RE = re.compile(r"vimeo\.com/(?:video/)?(\d+)")


@dataclass
class Preview:
    title: str = ""
    description: str = ""
    site_name: str = ""
    image_url: str = ""
    embed_url: str = ""
    image_bytes: bytes | None = field(default=None, repr=False)
    image_name: str = ""


class _HeadParser(HTMLParser):
    """Pull <meta> properties and <title> out of a (possibly truncated) page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "meta":
            key = (attributes.get("property") or attributes.get("name") or "").lower()
            content = attributes.get("content")
            if key and content:
                # First tag wins: OpenGraph puts the canonical one first.
                self.meta.setdefault(key, content)
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title and not self.title:
            self.title = data.strip()


def _is_public(host: str) -> bool:
    """Reject anything resolving to a private, loopback or reserved address.

    blob fetches URLs on behalf of whoever pastes them, which is a server-side
    request forgery primitive: without this, pasting http://adguard:3000/ or
    http://169.254.169.254/ would make blob fetch it from inside the docker
    network and render the result.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global or address.is_multicast:
            return False
    return True


def _get(url: str, *, accept: str) -> requests.Response | None:
    """GET with redirects followed one hop at a time, re-checking each host.

    requests would happily follow a redirect from a public host to
    http://127.0.0.1/, so allow_redirects is off and every hop is validated.
    """
    headers = {"User-Agent": USER_AGENT, "Accept": accept}
    for _ in range(MAX_REDIRECTS):
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return None
        if not _is_public(parsed.hostname):
            log.warning("link preview refused non-public host %s", parsed.hostname)
            return None
        response = requests.get(
            url, headers=headers, timeout=TIMEOUT, stream=True, allow_redirects=False
        )
        if response.is_redirect and response.headers.get("location"):
            url = urljoin(url, response.headers["location"])
            response.close()
            continue
        return response
    return None


def _read_capped(response: requests.Response, limit: int) -> bytes:
    chunks, total = [], 0
    for chunk in response.iter_content(16 * 1024):
        chunks.append(chunk)
        total += len(chunk)
        if total >= limit:
            break
    return b"".join(chunks)[:limit]


def _known_video(url: str) -> tuple[str, str]:
    """Return (embed_url, fallback_thumbnail) for hosts we can embed."""
    if match := YOUTUBE_RE.search(url):
        video_id = match.group(1)
        return (
            f"https://www.youtube-nocookie.com/embed/{video_id}",
            f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        )
    if match := VIMEO_RE.search(url):
        return f"https://player.vimeo.com/video/{match.group(1)}", ""
    return "", ""


def _oembed(url: str) -> dict:
    """Ask the video host for its own metadata.

    YouTube serves scrapers a consent interstitial with no OpenGraph tags, so
    parsing the watch page gives nothing. The oEmbed endpoint answers plain
    JSON with the real title and thumbnail.
    """
    if "youtube.com" in url or "youtu.be" in url:
        endpoint = f"https://www.youtube.com/oembed?format=json&url={quote(url, safe='')}"
    elif "vimeo.com" in url:
        endpoint = f"https://vimeo.com/api/oembed.json?url={quote(url, safe='')}"
    else:
        return {}
    try:
        response = _get(endpoint, accept="application/json")
        if response is None or response.status_code != 200:
            return {}
        with response:
            return json.loads(_read_capped(response, 64 * 1024))
    except (requests.RequestException, ValueError) as exc:
        log.info("oembed failed for %s: %s", url, exc)
        return {}


def _fetch_image(url: str) -> tuple[bytes, str] | None:
    """Download a thumbnail so the feed stays local and survives link rot."""
    try:
        response = _get(url, accept="image/*")
        if response is None or response.status_code != 200:
            return None
        with response:
            if not response.headers.get("content-type", "").startswith("image/"):
                return None
            data = _read_capped(response, MAX_IMAGE_BYTES)
    except requests.RequestException as exc:
        log.info("preview image fetch failed for %s: %s", url, exc)
        return None
    name = urlparse(url).path.rsplit("/", 1)[-1] or "preview"
    return data, name


def fetch(url: str) -> Preview:
    """Best effort. A blob is always saved, with or without a preview."""
    preview = Preview()
    embed_url, fallback_image = _known_video(url)
    preview.embed_url = embed_url

    try:
        response = _get(url, accept="text/html,application/xhtml+xml")
        if response is not None:
            with response:
                if response.headers.get("content-type", "").startswith("text/html"):
                    html = _read_capped(response, MAX_HTML_BYTES)
                    parser = _HeadParser()
                    parser.feed(html.decode(response.encoding or "utf-8", "replace"))
                    meta = parser.meta
                    preview.title = meta.get("og:title") or parser.title
                    preview.description = (
                        meta.get("og:description") or meta.get("description") or ""
                    )
                    preview.site_name = meta.get("og:site_name") or urlparse(url).hostname or ""
                    # urljoin(url, "") returns the page itself, which would
                    # shadow the fallback thumbnail, so only resolve a real tag.
                    if og_image := meta.get("og:image") or meta.get("twitter:image"):
                        preview.image_url = urljoin(url, og_image)
                    if not preview.embed_url and meta.get("og:video:url", "").startswith(
                        "https://"
                    ):
                        preview.embed_url = meta["og:video:url"]
    except requests.RequestException as exc:
        log.info("link preview failed for %s: %s", url, exc)

    if preview.embed_url:
        oembed = _oembed(url)
        preview.title = oembed.get("title") or preview.title
        preview.site_name = oembed.get("provider_name") or preview.site_name
        preview.image_url = oembed.get("thumbnail_url") or preview.image_url

    if not preview.site_name:
        preview.site_name = urlparse(url).hostname or ""

    image_source = preview.image_url or fallback_image
    if image_source:
        if fetched := _fetch_image(image_source):
            preview.image_bytes, preview.image_name = fetched

    return preview
