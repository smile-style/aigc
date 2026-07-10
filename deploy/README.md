# Docker Deployment

1. Copy the env template if you do not already have one:

```bash
cp deploy/.env.example deploy/.env
```

2. Edit `deploy/.env` and set `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, MySQL passwords, and the LLM settings.

3. Build and start:

```bash
docker compose -f deploy/docker-compose.yml up -d --build
```

The web container waits for MySQL to become healthy, then runs Django migrations before starting Gunicorn.

4. Open:

```text
http://<server-ip-or-domain>:8080/
```

Runtime data is stored in Docker volumes:

- `mysql_data`: primary application data, including projects, outlines, scripts, and storyboard prompts.
- `aigc_logs`: LLM request logs, including `/app/logs/llm.log`.
- `aigc_workspace`: reserved for compatibility and temporary workspace files.

Stop the service:

```bash
docker compose -f deploy/docker-compose.yml down
```