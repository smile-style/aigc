ARG BASE_IMAGE=registry.cn-hangzhou.aliyuncs.com/docker-registry-cache/aigc-studio-base:py3.12-v1
FROM ${BASE_IMAGE}

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip \
    && pip install -r /app/requirements.txt

COPY . /app

RUN python manage.py collectstatic --noinput

EXPOSE 8000

CMD ["sh", "-c", "exec gunicorn aigc_site.wsgi:application --bind 0.0.0.0:8000 --workers \"${GUNICORN_WORKERS}\" --timeout \"${GUNICORN_TIMEOUT}\""]
