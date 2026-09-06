"""Minimal Django settings for the telemetry receiver."""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent      # .../backend
REPO_DIR = BASE_DIR.parent                             # .../Project

# Dev only. Never use this key on anything exposed.
SECRET_KEY = "dev-only-not-secret-telemetry-lab"
DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "tracker",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
]

# Open enough that index.html also works from file:// or another dev server.
CORS_ALLOW_ALL_ORIGINS = True

ROOT_URLCONF = "telemetry_project.urls"
WSGI_APPLICATION = "telemetry_project.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": False,
        "OPTIONS": {"context_processors": []},
    },
]

# SQLite needs no daemon; only ENGINE/NAME change to move to Postgres.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    # django.contrib.auth is not installed, so DRF must not reach for AnonymousUser.
    "UNAUTHENTICATED_USER": None,
}

FRONTEND_DIR = REPO_DIR / "frontend"

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
