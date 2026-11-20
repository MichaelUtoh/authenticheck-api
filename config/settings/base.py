"""Settings shared by every environment. Environment-specific values live in sibling modules."""

from pathlib import Path

from corsheaders.defaults import default_headers
import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
_env_file = BASE_DIR / ".env"
if _env_file.exists():
    environ.Env.read_env(_env_file)  # real environment variables always win

# --- Core -----------------------------------------------------------------------------------
SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])
ADMIN_URL = env("DJANGO_ADMIN_URL", default="admin/")  # change in production to reduce noise
API_DOCS_ENABLED = env.bool("API_DOCS_ENABLED", default=False)

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]
THIRD_PARTY_APPS = [
    "rest_framework",
    "drf_spectacular",
    "corsheaders",
    "django_otp",
    "django_otp.plugins.otp_totp",
]
LOCAL_APPS = ["apps.core", "apps.accounts", "apps.tenants", "apps.audit", "apps.identity", "apps.catalogue", "apps.issuance", "apps.verification", "apps.risk", "apps.analytics", "apps.platform", "apps.webhooks", "apps.integrations"]
INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# Custom user model from day one: swapping it later is painful.
AUTH_USER_MODEL = "accounts.User"

OTP_TOTP_ISSUER = env("OTP_TOTP_ISSUER", default="AuthentiCheck")

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "apps.core.middleware.RequestIDMiddleware",
    "apps.core.metrics.VerifyMetricsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.core.middleware.TenantContextMiddleware",
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
            ]
        },
    }
]

# --- Database -------------------------------------------------------------------------------
DATABASES = {"default": env.db("DATABASE_URL")}
# Each request runs in one transaction: a failed audit write rolls the whole mutation back.
DATABASES["default"]["ATOMIC_REQUESTS"] = True
DATABASES["default"]["CONN_MAX_AGE"] = 60
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"  # models define explicit UUID primary keys

# --- Cache / Celery -------------------------------------------------------------------------
REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": REDIS_URL}
}

CELERY_BROKER_URL = env("CELERY_BROKER_URL", default=REDIS_URL)
CELERY_TASK_ACKS_LATE = (
    True  # a task lost with its worker is redelivered, so tasks must be idempotent
)
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_TIME_LIMIT = 600
CELERY_TASK_SOFT_TIME_LIMIT = 540
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TIMEZONE = "UTC"
# No Celery result backend on purpose: job state is persisted in our own tables.

# --- Auth / security ------------------------------------------------------------------------
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",  # fallback for existing hashes
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

# --- JWT / refresh tokens -------------------------------------------------------------------
JWT_SIGNING_KEY = env("JWT_SIGNING_KEY")
JWT_PREVIOUS_KEYS = env.list("JWT_PREVIOUS_KEYS", default=[])
JWT_ISSUER = env("JWT_ISSUER", default="authenticheck")
JWT_AUDIENCE = env("JWT_AUDIENCE", default="authenticheck:api")
JWT_LEEWAY_SECONDS = env.int("JWT_LEEWAY_SECONDS", default=30)
ACCESS_TTL_MINUTES = env.int("ACCESS_TTL_MINUTES", default=10)
ADMIN_ACCESS_TTL_MINUTES = env.int("ADMIN_ACCESS_TTL_MINUTES", default=5)
REFRESH_IDLE_DAYS = env.int("REFRESH_IDLE_DAYS", default=7)
REFRESH_ABSOLUTE_DAYS = env.int("REFRESH_ABSOLUTE_DAYS", default=30)
ADMIN_REFRESH_IDLE_MINUTES = env.int("ADMIN_REFRESH_IDLE_MINUTES", default=60)
ADMIN_REFRESH_ABSOLUTE_HOURS = env.int("ADMIN_REFRESH_ABSOLUTE_HOURS", default=12)
REFRESH_GRACE_SECONDS = env.int("REFRESH_GRACE_SECONDS", default=10)
RECENT_AUTH_SECONDS = env.int("RECENT_AUTH_SECONDS", default=300)
INVITATION_TTL_DAYS = env.int("INVITATION_TTL_DAYS", default=7)
# --- Email (invitations, account emails) ---------------------------------------------------
# local.py prints emails to the console and test.py keeps them in memory; every other environment
# uses SMTP configured here. Without EMAIL_HOST, mail cannot be delivered.
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", default="localhost")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
EMAIL_TIMEOUT = 10
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="AuthentiCheck <no-reply@localhost>")

# Base URL of the frontend; invitation emails link to {FRONTEND_URL}/accept-invite?token=...
FRONTEND_URL = env("FRONTEND_URL", default="http://localhost:3000")
# Public consumer verify domain: the GS1 Digital Link resolver redirects to {VERIFY_BASE_URL}/v/{code}.
# Printed on labels, so it must stay stable; defaults to the frontend.
VERIFY_BASE_URL = env("VERIFY_BASE_URL", default=FRONTEND_URL)
RESET_TOKEN_TTL_MINUTES = env.int("RESET_TOKEN_TTL_MINUTES", default=30)
SUPPORT_GRANT_MAX_HOURS = env.int("SUPPORT_GRANT_MAX_HOURS", default=4)
MFA_CHALLENGE_MAX_ATTEMPTS = env.int("MFA_CHALLENGE_MAX_ATTEMPTS", default=5)
REFRESH_COOKIE_NAME = env("REFRESH_COOKIE_NAME", default="refresh_token")
REFRESH_COOKIE_SECURE = env.bool("REFRESH_COOKIE_SECURE", default=True)
REFRESH_COOKIE_DOMAIN = env("REFRESH_COOKIE_DOMAIN", default=None)
REFRESH_COOKIE_SAMESITE = env("REFRESH_COOKIE_SAMESITE", default="Strict")
TRUSTED_PROXY_COUNT = env.int("TRUSTED_PROXY_COUNT", default=0)
DATA_UPLOAD_MAX_MEMORY_SIZE = (
    1_000_000  # non-file request bodies; file uploads get their own limits
)

CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
CORS_ALLOW_CREDENTIALS = True
# Custom request headers the frontend sends must be listed or the browser preflight fails.
CORS_ALLOW_HEADERS = (*default_headers, "idempotency-key")
CORS_EXPOSE_HEADERS = ["X-Request-ID", "Idempotent-Replayed", "Retry-After"]
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# --- i18n -----------------------------------------------------------------------------------
LANGUAGE_CODE = "en-gb"
TIME_ZONE = "UTC"  # all stored and server times are UTC; clients render Africa/Lagos etc.
USE_I18N = True
USE_TZ = True

# --- Static ---------------------------------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# --- Media (uploaded product images) --------------------------------------------------------
# Production: set MEDIA_BUCKET to use any S3-compatible store (AWS S3, Cloudflare R2, MinIO) and
# MEDIA_CDN_DOMAIN to the CDN host in front of it. Local dev falls back to MEDIA_ROOT on disk,
# served by the dev server; set MEDIA_URL to an absolute URL (http://localhost:8000/media/) so
# clients on another origin can load it. Never rely on local disk in production: it is ephemeral
# and not shared between instances.
MEDIA_ROOT = BASE_DIR / "media"
MEDIA_URL = env("MEDIA_URL", default="/media/")
MEDIA_BUCKET = env("MEDIA_BUCKET", default="")
if MEDIA_BUCKET:
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": MEDIA_BUCKET,
            "endpoint_url": env("MEDIA_ENDPOINT_URL", default=None),
            "region_name": env("MEDIA_REGION", default=None),
            "access_key": env("MEDIA_ACCESS_KEY_ID", default=None),
            "secret_key": env("MEDIA_SECRET_ACCESS_KEY", default=None),
            "custom_domain": env("MEDIA_CDN_DOMAIN", default=None),
            "default_acl": None,  # bucket stays private; the CDN reads it
            "querystring_auth": False,
            "file_overwrite": False,
            # Object names are random UUIDs and never reused, so they can be cached forever.
            "object_parameters": {"CacheControl": "public, max-age=31536000, immutable"},
        },
    }

# Uploaded images that no product references are deleted after this many hours.
UNATTACHED_IMAGE_TTL_HOURS = env.int("UNATTACHED_IMAGE_TTL_HOURS", default=24)
CELERY_BEAT_SCHEDULE = {
    "cleanup-unattached-product-images": {
        "task": "apps.catalogue.tasks.cleanup_unattached_images",
        "schedule": 3600.0,
    },
}

# --- DRF ------------------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.identity.authentication.JWTAuthentication",
        "apps.identity.authentication.APIKeyAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["apps.identity.permissions.IsAuthenticatedSession"],  # deny by default
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],  # opt in to multipart per view
    "EXCEPTION_HANDLER": "apps.core.exceptions.api_exception_handler",
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.StandardPagination",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "60/min",
        "user": "600/min",
        "login_ip": "30/15min",
        "login_user": "5/15min",
        "mfa": "10/15min",
        "password_reset_email": "3/hour",
        "password_reset_ip": "10/hour",
        "register": "5/hour",
        "invitation_accept": "10/hour",
        "invitation_preview": "30/hour",
        "invitation_send": "30/hour",
        "refresh": "60/min",
        "reauth": "5/15min",
        "verify_ip": "30/min",
        "report_ip": "5/hour",
    },
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "AuthentiCheck API",
    "DESCRIPTION": "Product verification and traceability for manufacturers and consumers.",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "ENUM_NAME_OVERRIDES": {
        "TenantStatusEnum": "apps.tenants.models.Tenant.Status",
        "MembershipStatusEnum": "apps.tenants.models.Membership.Status",
        "MembershipRoleEnum": "apps.tenants.models.Membership.Role",
        "InvitationStatusEnum": "apps.identity.models.Invitation.Status",
        "SessionRevocationReasonEnum": "apps.identity.models.AuthSession.RevocationReason",
        "BatchStatusEnum": "apps.catalogue.models.Batch.Status",
        "ProductStatusEnum": "apps.catalogue.models.Product.Status",
        "JobStatusEnum": "apps.issuance.models.IssuanceJob.Status",
        "CodeStatusEnum": "apps.issuance.models.Code.Status",
        "ScanResultEnum": "apps.verification.models.ScanEvent.Result",
        "RiskFlagKindEnum": "apps.risk.models.RiskFlag.Kind",
        "RiskFlagSeverityEnum": "apps.risk.models.RiskFlag.Severity",
        "CaseStatusEnum": "apps.risk.models.Case.Status",
        "WebhookEndpointStatusEnum": "apps.webhooks.models.WebhookEndpoint.Status",
        "WebhookDeliveryStatusEnum": "apps.webhooks.models.WebhookDelivery.Status",
    },
    "SECURITY": [{"bearerAuth": []}],
}

AFRICASTALKING_API_KEY = env("AFRICASTALKING_API_KEY", default="")

RISK_REPEATED_SCAN_THRESHOLD = env.int("RISK_REPEATED_SCAN_THRESHOLD", default=10)

# --- Logging --------------------------------------------------------------------------------
# Shown on the platform ops panel; set by whoever runs the deployment (ISO 8601, optional).
TLS_CERT_EXPIRES_AT = env("TLS_CERT_EXPIRES_AT", default="")
LAST_RESTORE_TEST_AT = env("LAST_RESTORE_TEST_AT", default="")

LOG_FORMAT = env("LOG_FORMAT", default="json")  # "json" or "console"
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"request_id": {"()": "apps.core.logs.RequestIDFilter"}},
    "formatters": {
        "json": {
            "()": "pythonjsonlogger.jsonlogger.JsonFormatter",
            "format": "%(asctime)s %(levelname)s %(name)s %(request_id)s %(message)s",
        },
        "console": {"format": "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"},
    },
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "filters": ["request_id"],
            "formatter": LOG_FORMAT,
        }
    },
    "root": {"handlers": ["stdout"], "level": env("LOG_LEVEL", default="INFO")},
}
