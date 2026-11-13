FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DJANGO_SETTINGS_MODULE=atlas.settings
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ .
RUN DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput \
    && useradd --system --uid 10001 atlas
USER atlas

EXPOSE 8000
CMD ["sh", "-c", "python manage.py migrate --noinput && exec gunicorn atlas.wsgi:application --bind 0.0.0.0:8000 --workers 3 --worker-class gthread --threads 4 --no-control-socket"]
