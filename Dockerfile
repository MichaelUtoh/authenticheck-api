FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

ARG REQUIREMENTS=base
COPY requirements/ requirements/
RUN pip install -r requirements/${REQUIREMENTS}.txt

COPY . .
# Gather admin/DRF static assets for WhiteNoise. Throwaway keys: collectstatic needs settings to load but no secrets.
RUN DJANGO_SETTINGS_MODULE=config.settings.production DJANGO_SECRET_KEY=build-only JWT_SIGNING_KEY=build-only \
    DATABASE_URL=sqlite:////tmp/build.db python manage.py collectstatic --noinput
RUN useradd --system --create-home app && chown -R app /app
USER app

EXPOSE 8000
CMD ["./scripts/start.sh"]
