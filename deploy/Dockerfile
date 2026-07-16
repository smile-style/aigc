FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_DEBUG=false \
    DJANGO_STATIC_ROOT=/app/staticfiles \
    DJANGO_MEDIA_ROOT=/app/media \
    WORKSPACE_DIR=/app/workspace \
    LLM_LOG_PATH=/app/logs/llm.log \
    GUNICORN_WORKERS=2 \
    GUNICORN_TIMEOUT=360

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ffmpeg \
    && ffmpeg -version \
    && ffprobe -version \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip \
    && pip install -r /app/requirements.txt

COPY . /app

RUN mkdir -p /app/workspace /app/logs /app/media /app/staticfiles \
    && python manage.py collectstatic --noinput

EXPOSE 8000

CMD ["sh", "-c", "exec gunicorn aigc_site.wsgi:application --bind 0.0.0.0:8000 --workers \"${GUNICORN_WORKERS}\" --timeout \"${GUNICORN_TIMEOUT}\""]
