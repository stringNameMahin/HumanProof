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
key_file = BASE_DIR / ".analyst-key"
HUMANPROOF_ANALYST_KEY = os.environ.get("HUMANPROOF_ANALYST_KEY", "")
if not HUMANPROOF_ANALYST_KEY and key_file.exists():
    HUMANPROOF_ANALYST_KEY = key_file.read_text(encoding="ascii").strip()

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
