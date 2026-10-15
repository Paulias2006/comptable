import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

def load_env():
    env_file = BASE_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip().lstrip("\ufeff"), value.strip().strip('"').strip("'"))

load_env()
DEBUG = os.getenv("DJANGO_DEBUG", "0") == "1"
DJANGO_SETUP_TOKEN = os.getenv("DJANGO_SETUP_TOKEN", "").strip()
render_hostname = os.getenv("RENDER_EXTERNAL_HOSTNAME", "").strip()
if render_hostname and DEBUG:
    raise ImproperlyConfigured("DJANGO_DEBUG doit rester à 0 sur Render.")
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "").strip()
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "local-only-key-change-before-network-use"
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY doit être configurée en production.")
if not DEBUG and (SECRET_KEY == "local-only-key-change-before-network-use" or len(SECRET_KEY) < 50):
    raise ImproperlyConfigured("DJANGO_SECRET_KEY doit contenir au moins 50 caractères aléatoires en production.")
ALLOWED_HOSTS = [h.strip() for h in os.getenv("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if h.strip()]
if render_hostname and render_hostname not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(render_hostname)
CSRF_TRUSTED_ORIGINS = [f"https://{render_hostname}"] if render_hostname else []
INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "ledger.apps.LedgerConfig", "axes",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware", "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware", "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware", "django.contrib.messages.middleware.MessageMiddleware",
    "axes.middleware.AxesMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware", "ledger.middleware.CompanyContextMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [BASE_DIR / "templates"], "APP_DIRS": True,
    "OPTIONS": {"context_processors": ["django.template.context_processors.request", "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages", "ledger.context_processors.workspace"]}}]
WSGI_APPLICATION = "config.wsgi.application"
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if DATABASE_URL:
    u = urlparse(DATABASE_URL)
    if u.scheme not in {"postgresql", "postgres"} or not (u.hostname and u.username and u.password and u.path.strip("/")):
        raise ImproperlyConfigured("DATABASE_URL doit être une URL PostgreSQL complète et valide.")
    db_options = {"connect_timeout": 5}
    query_options = parse_qs(u.query)
    for option in ("sslmode", "channel_binding"):
        if query_options.get(option):
            db_options[option] = query_options[option][-1]
    DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql", "NAME": unquote(u.path.lstrip("/")), "USER": unquote(u.username or ""), "PASSWORD": unquote(u.password or ""), "HOST": u.hostname or "localhost", "PORT": u.port or 5432, "OPTIONS": db_options}}
elif DEBUG:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3", "OPTIONS": {"timeout": 20}}}
else:
    raise ImproperlyConfigured("DATABASE_URL PostgreSQL doit être configurée en production.")
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]
LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "Africa/Lome"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_ROOT = Path(os.getenv("PRIVATE_MEDIA_ROOT", BASE_DIR / "private_media"))
MEDIA_URL = "/private-media/"  # Never served directly; downloads pass through permission checks.
cloudinary_url = os.getenv("CLOUDINARY_URL", "").strip()
if cloudinary_url:
    parsed_cloudinary_url = urlparse(cloudinary_url)
    if parsed_cloudinary_url.scheme != "cloudinary" or not all(
        (parsed_cloudinary_url.hostname, parsed_cloudinary_url.username, parsed_cloudinary_url.password)
    ):
        raise ValueError("CLOUDINARY_URL doit être copiée depuis Cloudinary au format cloudinary://clé:secret@cloud.")
    cloudinary_values = {
        "cloud_name": parsed_cloudinary_url.hostname,
        "api_key": unquote(parsed_cloudinary_url.username),
        "api_secret": unquote(parsed_cloudinary_url.password),
    }
else:
    cloudinary_values = {
        "cloud_name": os.getenv("CLOUDINARY_CLOUD_NAME", "").strip(),
        "api_key": os.getenv("CLOUDINARY_API_KEY", "").strip(),
        "api_secret": os.getenv("CLOUDINARY_API_SECRET", "").strip(),
    }
CLOUDINARY_CONFIGURED = all(cloudinary_values.values())
if not DEBUG and not CLOUDINARY_CONFIGURED:
    raise ImproperlyConfigured("Cloudinary doit être configuré en production pour conserver les pièces jointes.")
if CLOUDINARY_CONFIGURED:
    import cloudinary

    cloudinary.config(**cloudinary_values, secure=True)
    STORAGES = {
        "default": {"BACKEND": "ledger.storage.CloudinaryPrivateStorage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
    }
else:
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
    }
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
X_FRAME_OPTIONS = "DENY"
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = timedelta(minutes=15)
AXES_LOCKOUT_PARAMETERS = ["username", "ip_address"]
AXES_IPWARE_PROXY_COUNT = 1
AXES_ENABLE_ADMIN = True
DATA_UPLOAD_MAX_MEMORY_SIZE = 40 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 4 * 1024 * 1024
