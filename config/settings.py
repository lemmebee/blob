"""Settings for blob.

Everything mutable lives under DATA_DIR, which is a docker named volume in
production (homelab/apps/blob.yaml). The image itself is disposable: rebuild
it whenever, the blobs stay.
"""

import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _secret_key() -> str:
    """Env var wins, else generate once and keep it next to the database.

    A homelab box has no secret manager, and a key regenerated on every boot
    would invalidate every signed cookie and CSRF token on restart.
    """
    if env_key := os.environ.get("SECRET_KEY"):
        return env_key
    path = DATA_DIR / "secret_key"
    if not path.exists():
        path.write_text(secrets.token_urlsafe(64))
        path.chmod(0o600)
    return path.read_text().strip()


SECRET_KEY = _secret_key()
DEBUG = os.environ.get("DEBUG", "0") == "1"

# Traefik routes any host whose first label is `blob` (blob.localhost,
# blob.local, blob.<lan-ip>.nip.io), so the exact Host header is not knowable
# here, and traefik is the only way in. Narrow this before exposing blob
# outside the LAN.
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "*").split(",")
CSRF_TRUSTED_ORIGINS = [
    origin for origin in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if origin
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "blobs",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATA_DIR / "blob.sqlite3",
        "OPTIONS": {
            # WAL keeps the feed readable while an upload writes. Without a
            # busy timeout, two gunicorn workers writing at once raise
            # "database is locked" instead of waiting their turn.
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
            "timeout": 20,
        },
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("TZ", "UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "/media/"
MEDIA_ROOT = DATA_DIR / "media"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Uploads never buffer fully in memory: anything over 2 MB streams to a temp
# file, and the hard ceiling is enforced in BlobForm.
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024

# blob-specific knobs.
BLOB_MAX_TEXT_CHARS = int(os.environ.get("BLOB_MAX_TEXT_CHARS", 1_000_000))
BLOB_MAX_IMAGE_BYTES = int(os.environ.get("BLOB_MAX_IMAGE_BYTES", 25 * 1024 * 1024))
BLOB_THUMB_MAX_PX = 1200
BLOB_PAGE_SIZE = 20

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
