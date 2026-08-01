# blob

A box you paste things into. Text up to the configured storage limit, images,
links. They stack up in one reverse-chronological feed and stay there.

Deployed from [homelab](../homelab) at `http://blob.localhost`
(`http://blob.local` from the rest of the LAN).

## One box

There is a single input. What you put in it decides what the blob becomes:

| You put in            | You get                                                          |
|-----------------------|------------------------------------------------------------------|
| anything typed        | text, wrapped, clamped at ~22rem in the feed, whole on its own page |
| nothing but a URL     | a link card: title, description, site, thumbnail, fetched once     |
| a YouTube/Vimeo URL   | click-to-play embed over a locally cached thumbnail                |
| Ctrl+V on a screenshot | the image attaches straight from the clipboard, one after another |
| the image icon        | same, via a file picker (jpeg/png/webp/gif/heic), multi-select ok  |

Text and images go in together and the caption stays with the pictures. There
is no cap on how many images a blob carries: paste five screenshots in a row
and they all ride along. The feed card previews four with a `+n` counter, the
blob's own page shows every one at full size. Ctrl/Cmd+Enter sends.

## Sub-blobs

Every blob has its own page, and that page has the same box at the bottom.
Anything you put in it hangs off that blob: a follow-up, a second screenshot,
the link you found later. Sub-blobs never appear in the feed; the parent card
shows how many are waiting. Deleting a blob takes its sub-blobs and every
attached file with it.

## Editing

The pencil on any blob opens the same box with its contents in it. Text can be
rewritten, images ticked for removal, more images added. Typing a bare URL over
plain text turns the blob into a link and fetches its preview; replacing a URL
with words throws the old link metadata and its cached thumbnail away. Edited
blobs are marked, and a blob can never be emptied to nothing.

## Design notes

**No browser dialogs.** Deleting asks in an in-page `<dialog>` styled like
the rest of the app, not through `confirm()`. The form carries the question in
`data-confirm` and the script routes it through the modal; with JavaScript off
the form simply submits.

**Images open over the page.** Clicking a picture on a blob's page opens it
full size in a `<dialog>`; clicking anywhere beside it, or Escape, closes it.
The markup is still a plain link to the file, so it degrades to opening the
image directly.

**A card is one link, to its own page.** That includes link blobs: clicking the
card opens the blob, not the site. Going to the site is a separate, explicit
act, the arrow in the footer. The card body is plain markup with a single
absolutely positioned anchor over it, because nesting an `<a>` inside an `<a>`
is invalid and the inner one steals the click.

**Light and dark.** The system preference is the default; the toggle in the bar
overrides it either way and is stored in `localStorage`, applied by a tiny
inline script before first paint so a light-theme user never sees a dark flash.

**Nothing a user pastes can break the layout.** Text renders with
`white-space: pre-wrap` and `overflow-wrap: anywhere`, so a 5000-character
string with no spaces wraps instead of widening the page. Long text is clamped
with a fade and expands via a checkbox, no JavaScript. Feed cards use
`content-visibility: auto`, so a few thousand blobs still scroll at 60fps.

**Video hosts are asked, not scraped.** YouTube serves scrapers a consent
interstitial with no OpenGraph tags, so the watch page yields nothing; the
oEmbed endpoint answers plain JSON with the real title and thumbnail.

**Link previews are fetched once, at save time.** Rendering the feed never
touches the network, a dead link still shows what it used to be, and the
thumbnail is downloaded and stored locally, so viewing the feed does not tell
every site you ever saved that you are looking at it.

**Fetching a user-supplied URL is server-side request forgery by design**, so
`blobs/preview.py` resolves the host and refuses anything private, loopback,
link-local or reserved. Redirects are followed one hop at a time and each hop
is re-checked, because `requests` would otherwise happily follow a public URL
to `http://127.0.0.1/`. Without this, pasting `http://adguard:3000/` would make
blob fetch a neighbouring container from inside the docker network.

**Video embeds do not load until clicked.** The feed shows a stored thumbnail;
the third-party iframe (youtube-nocookie, `referrerpolicy=no-referrer`) is
created on click.

**Limits are on disk, not on screen.** Text is unlimited to read but capped at
1,000,000 characters to store; images at 25 MB each, with no cap on how many; a
fetched page at 512 KB and a fetched thumbnail at 5 MB. Every one of those is a
setting, see the table below.

## State

Everything lives in `DATA_DIR` (`/data` in the container, the `blob-data`
volume):

```text
/data
├── blob.sqlite3     # blobs
├── secret_key       # generated on first boot if SECRET_KEY is unset
└── media/blobs/     # uploads, thumbnails, cached link previews
```

The image is disposable. `make rollout app=blob` rebuilds it and keeps all of
the above; `make clean` deletes the volume and there is no other copy.

## Configuration

| Variable                | Default        | Meaning                                     |
|-------------------------|----------------|---------------------------------------------|
| `DATA_DIR`              | `./data`       | Where state lives (`/data` in the container) |
| `SECRET_KEY`            | generated      | Django signing key, persisted under DATA_DIR |
| `DEBUG`                 | `0`            | `1` for tracebacks and unhashed static files |
| `ALLOWED_HOSTS`         | `*`            | Traefik is the only ingress; narrow if exposed |
| `CSRF_TRUSTED_ORIGINS`  | empty          | Comma separated, needed only behind HTTPS    |
| `BLOB_MAX_TEXT_CHARS`   | `1000000`      | Rejects longer pastes                        |
| `BLOB_MAX_IMAGE_BYTES`  | `26214400`     | 25 MB, per attached image                    |
| `BLOB_MAX_PAGE_BYTES`   | `524288`       | How much of a page a link preview reads      |
| `BLOB_MAX_PREVIEW_BYTES`| `5242880`      | Cap on a downloaded preview thumbnail        |
| `WEB_CONCURRENCY`       | `3`            | gunicorn workers; SQLite serialises writers  |
| `TZ`                    | `UTC`          | Timestamps in the feed                       |

## Local development

```sh
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
DEBUG=1 .venv/bin/python manage.py runserver
```

Admin at `/admin` after `manage.py createsuperuser`: search, edit and bulk
delete blobs without touching the database.

## Layout

```text
blob/
├── config/          # settings, root urls, wsgi
├── blobs/
│   ├── models.py    # Blob (self-FK for sub-blobs) + BlobImage (many per blob)
│   ├── preview.py   # OpenGraph + oEmbed fetch, SSRF guard, YouTube/Vimeo embeds
│   ├── forms.py     # multi-file upload, size limits, link detection
│   ├── views.py     # feed (top-level only), blob page + thread, edit, delete
│   └── templates/
├── static/          # one stylesheet, one small script (theme, paste, scroll,
│                    #   infinite feed, confirm dialog, lightbox, click-to-play)
└── entrypoint.sh    # migrate, then gunicorn
```
