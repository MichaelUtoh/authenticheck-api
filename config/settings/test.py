import os

os.environ.setdefault(
    "DJANGO_SECRET_KEY", "test-only-secret-key-not-for-production-0123456789abcdef"
)
os.environ.setdefault(
    "DATABASE_URL", "postgres://authenticheck:authenticheck@localhost:5432/authenticheck"
)
os.environ.setdefault(
    "JWT_SIGNING_KEY", "test-only-jwt-signing-key-at-least-32-bytes!!"
)

from .base import *  # noqa: E402

DEBUG = False

# Tests must never touch a real bucket, whatever .env says: always use local disk.
MEDIA_BUCKET = ""
STORAGES = {
    **STORAGES,
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
}
ALLOWED_HOSTS = ["testserver", "localhost"]
API_DOCS_ENABLED = True

INSTALLED_APPS = INSTALLED_APPS + ["apps.testsupport"]
MIGRATION_MODULES = {"testsupport": None}  # test-only app: no migrations, table made in conftest

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # fast, tests only
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
LOGGING["handlers"]["stdout"]["formatter"] = "console"
