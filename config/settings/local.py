from .base import *

DEBUG = env.bool("DJANGO_DEBUG", default=True)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1", "[::1]"])
API_DOCS_ENABLED = env.bool("API_DOCS_ENABLED", default=True)
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
LOGGING["handlers"]["stdout"]["formatter"] = env("LOG_FORMAT", default="console")

MIDDLEWARE = [m for m in MIDDLEWARE if m != "django.middleware.csrf.CsrfViewMiddleware"]
