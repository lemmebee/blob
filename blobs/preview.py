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
from io import BytesIO
from urllib.parse import quote, urljoin, urlparse

import requests
from django.conf import settings
from PIL import Image, UnidentifiedImageError
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

log = logging.getLogger(__name__)

TIMEOUT = (3, 5)  # connect, read
MAX_REDIRECTS = 4
USER_AGENT = "Mozilla/5.0 (compatible; blob/1.0; +link-preview)"

# Formats a preview thumbnail may be stored as. The extension is taken from
# what Pillow decodes, never from the remote URL, so a renamed HTML payload
# cannot be served back from our own origin as active content.
PREVIEW_FORMATS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif"}

# Anchored at the host, so evil-youtube.com.attacker.tld does not pass for
# YouTube and get an embed pointing somewhere else entirely.
YOUTUBE_RE = re.compile(
    r"^https?://(?:[\w-]+\.)*(?:youtube\.com|youtube-nocookie\.com)/"
    r"(?:watch\?(?:.*&)?v=|embed/|shorts/|live/)([\w-]{11})"
    r"|^https?://youtu\.be/([\w-]{11})",
    re.IGNORECASE,
)
VIMEO_RE = re.compile(
    r"^https?://(?:[\w-]+\.)*vimeo\.com/(?:video/)?(\d+)", re.IGNORECASE
)


@dataclass
class Preview:
    title: str = ""
    description: str = ""
    site_name: str = ""
    image_url: str = ""
    embed_url: str = ""
    image_bytes: bytes | None = field(default=None, repr=False)
    # Extension of the decoded format, never taken from the remote URL.
    image_suffix: str = ""


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


class BlockedAddress(requests.RequestException):
    """Raised when a connection lands on an address we refuse to talk to."""


def _is_public(address: str) -> bool:
    parsed = ipaddress.ip_address(address)
    return parsed.is_global and not parsed.is_multicast


def _resolves_public(host: str) -> bool:
    """Cheap pre-flight: does this name point anywhere we are willing to go?

    blob fetches URLs on behalf of whoever pastes them, which is a server-side
    request forgery primitive: without this, pasting http://adguard:3000/ or
    http://169.254.169.254/ would make blob fetch it from inside the docker
    network and render the result.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    return all(_is_public(info[4][0]) for info in infos)


def _guard(sock):
    """Check the address actually connected to, not the one we looked up.

    The pre-flight check resolves the name, then urllib3 resolves it again
    before connecting. Anyone controlling the DNS answer can return a public
    address to the first lookup and a private one to the second. Inspecting
    the live socket closes that window: this runs on the raw TCP connection,
    before TLS and before a single byte of the request is written.
    """
    peer = sock.getpeername()[0]
    if not _is_public(peer):
        sock.close()
        raise BlockedAddress(f"refused connection to non-public address {peer}")
    return sock


class _GuardedHTTPConnection(HTTPConnection):
    def _new_conn(self):
        return _guard(super()._new_conn())


class _GuardedHTTPSConnection(HTTPSConnection):
    def _new_conn(self):
        return _guard(super()._new_conn())


class _GuardedHTTPConnectionPool(HTTPConnectionPool):
    ConnectionCls = _GuardedHTTPConnection


class _GuardedHTTPSConnectionPool(HTTPSConnectionPool):
    ConnectionCls = _GuardedHTTPSConnection


class GuardedAdapter(HTTPAdapter):
    """A requests adapter whose sockets refuse non-public peers."""

    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {
            "http": _GuardedHTTPConnectionPool,
            "https": _GuardedHTTPSConnectionPool,
        }


def _session() -> requests.Session:
    session = requests.Session()
    adapter = GuardedAdapter(max_retries=0)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def _get(url: str, *, accept: str) -> requests.Response | None:
    """GET with redirects followed one hop at a time, re-checking each host.

    requests would happily follow a redirect from a public host to
    http://127.0.0.1/, so allow_redirects is off and every hop is validated.
    """
    headers = {"User-Agent": USER_AGENT, "Accept": accept}
    with _session() as session:
        for _ in range(MAX_REDIRECTS):
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                return None
            if not _resolves_public(parsed.hostname):
                log.warning("link preview refused non-public host %s", parsed.hostname)
                return None
            response = session.get(
                url,
                headers=headers,
                timeout=TIMEOUT,
                stream=True,
                allow_redirects=False,
            )
            if response.is_redirect and response.headers.get("location"):
                url = urljoin(url, response.headers["location"])
                response.close()
                continue
            # The body is read while the session is still open, so the caller
            # gets a fully buffered response rather than a dead connection.
            response.content_capped = _read_capped(response, _cap_for(accept))
            response.close()
            return response
    return None


def _cap_for(accept: str) -> int:
    if accept.startswith("image/"):
        return settings.BLOB_MAX_PREVIEW_BYTES
    if "json" in accept:
        return 64 * 1024
    return settings.BLOB_MAX_PAGE_BYTES


def _read_capped(response: requests.Response, limit: int) -> bytes:
    chunks, total = [], 0
    for chunk in response.iter_content(16 * 1024):
        chunks.append(chunk)
        total += len(chunk)
        if total >= limit:
            break
    return b"".join(chunks)[:limit]


def _decode_image(data: bytes) -> tuple[bytes, str] | None:
    """Confirm the bytes really are an image and name them by what they are.

    Content-Type is the remote server's claim. Decoding is ours: a text/html
    payload renamed .jpg would otherwise be stored and served back from our
    own origin, where the extension decides the response type.
    """
    try:
        with Image.open(BytesIO(data)) as probe:
            probe.verify()
            fmt = probe.format
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return None
    suffix = PREVIEW_FORMATS.get(fmt or "")
    return (data, suffix) if suffix else None


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
    # Matched against the same anchored patterns as the embed itself, so a
    # lookalike host cannot steer us at someone else's oEmbed endpoint.
    if YOUTUBE_RE.match(url):
        endpoint = f"https://www.youtube.com/oembed?format=json&url={quote(url, safe='')}"
    elif VIMEO_RE.match(url):
        endpoint = f"https://vimeo.com/api/oembed.json?url={quote(url, safe='')}"
    else:
        return {}
    try:
        response = _get(endpoint, accept="application/json")
        if response is None or response.status_code != 200:
            return {}
        return json.loads(response.content_capped)
    except (requests.RequestException, ValueError) as exc:
        log.info("oembed failed for %s: %s", url, exc)
        return {}


def _fetch_image(url: str) -> tuple[bytes, str] | None:
    """Download a thumbnail so the feed stays local and survives link rot."""
    try:
        response = _get(url, accept="image/*")
        if response is None or response.status_code != 200:
            return None
        if not response.headers.get("content-type", "").startswith("image/"):
            return None
        data = response.content_capped
    except requests.RequestException as exc:
        log.info("preview image fetch failed for %s: %s", url, exc)
        return None
    decoded = _decode_image(data)
    if decoded is None:
        log.info("preview image from %s is not a decodable image", url)
    return decoded


def fetch(url: str) -> Preview:
    """Best effort. A blob is always saved, with or without a preview."""
    preview = Preview()
    embed_url, fallback_image = _known_video(url)
    preview.embed_url = embed_url

    try:
        response = _get(url, accept="text/html,application/xhtml+xml")
        if response is not None and response.headers.get("content-type", "").startswith(
            "text/html"
        ):
            parser = _HeadParser()
            parser.feed(
                response.content_capped.decode(response.encoding or "utf-8", "replace")
            )
            meta = parser.meta
            preview.title = meta.get("og:title") or parser.title
            preview.description = (
                meta.get("og:description") or meta.get("description") or ""
            )
            preview.site_name = meta.get("og:site_name") or urlparse(url).hostname or ""
            # urljoin(url, "") returns the page itself, which would shadow the
            # fallback thumbnail, so only resolve a real tag.
            if og_image := meta.get("og:image") or meta.get("twitter:image"):
                preview.image_url = urljoin(url, og_image)
            # og:video:url is deliberately not read: it lets any page name the
            # origin we would frame, so embeds stay limited to the hosts
            # _known_video recognises.
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
            preview.image_bytes, preview.image_suffix = fetched

    return preview
