ARG BASE_IMAGE=aigc-studio-base:py3.12-v1
FROM ${BASE_IMAGE}

WORKDIR /app

COPY . /app

RUN python manage.py collectstatic --noinput

EXPOSE 8000

CMD ["sh", "-c", "exec gunicorn aigc_site.wsgi:application --bind 0.0.0.0:8000 --workers \"${GUNICORN_WORKERS}\" --timeout \"${GUNICORN_TIMEOUT}\""]
