import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')
DEBUG = os.getenv('DJANGO_DEBUG', '1') == '1'
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'local-development-only-change-before-deploying')
if not DEBUG and (len(SECRET_KEY) < 40 or SECRET_KEY.startswith('local-')):
    raise ImproperlyConfigured('Set a random DJANGO_SECRET_KEY of at least 40 characters.')
ALLOWED_HOSTS = os.getenv('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1,[::1]').split(',')
CSRF_TRUSTED_ORIGINS = [x for x in os.getenv('CSRF_TRUSTED_ORIGINS', '').split(',') if x]
CSRF_FAILURE_VIEW = 'studio.views.csrf_failure'
INSTALLED_APPS = [
    'django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes',
    'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles',
    'studio',
]
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware', 'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware', 'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware', 'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware', 'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'studio.middleware.PrivateResponsesMiddleware',
]
ROOT_URLCONF = 'config.urls'
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'DIRS': [BASE_DIR / 'templates'],
              'APP_DIRS': True, 'OPTIONS': {'context_processors': [
                  'django.template.context_processors.request', 'django.contrib.auth.context_processors.auth',
                  'django.contrib.messages.context_processors.messages', 'studio.context.common',
              ]}}]
WSGI_APPLICATION = 'config.wsgi.application'
if os.getenv('POSTGRES_HOST'):
    DATABASES = {'default': {'ENGINE': 'django.db.backends.postgresql', 'NAME': os.getenv('POSTGRES_DB', 'studio'),
        'USER': os.getenv('POSTGRES_USER', 'studio'), 'PASSWORD': os.environ['POSTGRES_PASSWORD'],
        'HOST': os.environ['POSTGRES_HOST'], 'PORT': os.getenv('POSTGRES_PORT', '5432'), 'CONN_MAX_AGE': 60}}
else:
    if not DEBUG:
        raise ImproperlyConfigured('Production requires PostgreSQL (POSTGRES_HOST).')
    DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': BASE_DIR / 'db.sqlite3', 'OPTIONS': {'timeout': 30}}}
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
LANGUAGE_CODE = 'ru'
TIME_ZONE = os.getenv('APP_TIME_ZONE', 'Asia/Yekaterinburg')
USE_I18N = True
USE_TZ = True
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
STORAGES = {'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
            'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'}}
MEDIA_ROOT = Path(os.getenv('MEDIA_ROOT', str(BASE_DIR / 'media')))
# No public MEDIA_URL route: every file is served through an ownership check.
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_AGE = 60 * 60 * 8
CLASSROOM_HTTP = os.getenv('CLASSROOM_HTTP', '0') == '1'
SESSION_COOKIE_SECURE = not DEBUG and not CLASSROOM_HTTP
CSRF_COOKIE_SECURE = not DEBUG and not CLASSROOM_HTTP
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
SECURE_SSL_REDIRECT = not DEBUG and not CLASSROOM_HTTP and os.getenv('SECURE_SSL_REDIRECT', '1') == '1'
if os.getenv('TRUST_PROXY_HEADERS', '0') == '1':
    # Enable only when the web container can only be reached by the trusted TLS proxy.
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_HSTS_SECONDS = 31536000 if not DEBUG and not CLASSROOM_HTTP else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG and not CLASSROOM_HTTP
X_FRAME_OPTIONS = 'DENY'
DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
MESHY_MODE = os.getenv('MESHY_MODE', 'demo')
if MESHY_MODE not in ('demo', 'live'):
    raise ImproperlyConfigured('MESHY_MODE must be demo or live.')
MESHY_API_KEY = os.getenv('MESHY_API_KEY', '')
MESHY_AI_MODEL = os.getenv('MESHY_AI_MODEL', 'meshy-7.1')
MESHY_IMAGE_MODEL = os.getenv('MESHY_IMAGE_MODEL', 'nano-banana')
MESHY_ASSET_HOSTS = tuple(x.strip() for x in os.getenv('MESHY_ASSET_HOSTS', 'assets.meshy.ai').split(',') if x.strip())
REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
CELERY_BROKER_URL = REDIS_URL
CELERY_TASK_IGNORE_RESULT = True
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TASK_SOFT_TIME_LIMIT = 540
CELERY_TASK_TIME_LIMIT = 600
CELERY_BROKER_TRANSPORT_OPTIONS = {'visibility_timeout': 3600}
CELERY_BEAT_SCHEDULE = {'durable-queue': {'task': 'studio.tasks.dispatch', 'schedule': 15.0}}
if os.getenv('POSTGRES_HOST'):
    CACHES = {'default': {'BACKEND': 'django.core.cache.backends.redis.RedisCache', 'LOCATION': REDIS_URL}}
else:
    CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
