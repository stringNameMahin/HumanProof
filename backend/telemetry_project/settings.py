import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BASE_DIR.parent
sys.path.insert(0, str(REPO_DIR))

DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "local-humanproof-demo-key-change-before-deployment")
if not DEBUG and SECRET_KEY == "local-humanproof-demo-key-change-before-deployment":
    raise RuntimeError("Set DJANGO_SECRET_KEY before disabling debug mode.")
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost,[::1]").split(",")


def local_key(name, file_name):
    value = os.environ.get(name, "")
    key_path = BASE_DIR / file_name
    if not value and key_path.exists():
        value = key_path.read_text(encoding="ascii").strip()
    return value


HUMANPROOF_ANALYST_KEY = local_key("HUMANPROOF_ANALYST_KEY", ".analyst-key")
# Shared with edge/tls_edge.py so Django only trusts TLS metadata the edge signed.
HUMANPROOF_EDGE_KEY = local_key("HUMANPROOF_EDGE_KEY", ".edge-key")
HUMANPROOF_SESSION_RATE = os.environ.get("HUMANPROOF_SESSION_RATE", "60/hour")
HUMANPROOF_INGEST_RATE = os.environ.get("HUMANPROOF_INGEST_RATE", "240/min")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "tracker",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "tracker.api.BodyLimitMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
]
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = [value for value in os.environ.get("DJANGO_CORS_ORIGINS", "").split(",") if value]
ROOT_URLCONF = "telemetry_project.urls"
WSGI_APPLICATION = "telemetry_project.wsgi.application"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [], "APP_DIRS": False, "OPTIONS": {"context_processors": []},
}]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        "OPTIONS": {"timeout": 20},
    }
}
REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "tracker.api.exception_handler",
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "UNAUTHENTICATED_USER": None,
}
DATA_UPLOAD_MAX_MEMORY_SIZE = 256 * 1024
FRONTEND_DIR = REPO_DIR / "frontend"
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
