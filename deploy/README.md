# Docker Deployment

1. Copy the env template:

```bash
cp deploy/.env.example deploy/.env
```

2. Edit `deploy/.env` and set `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, and the LLM settings.

3. Build and start:

```bash
docker compose -f deploy/docker-compose.yml up -d --build
```

4. Open:

```text
http://<server-ip-or-domain>:52341/
```

Runtime data is stored in Docker volumes:

- `aigc_workspace`: local generated workspace JSON, currently `current.json`.
- `aigc_logs`: LLM request logs, including `/app/logs/llm.log`.

Stop the service:

```bash
docker compose -f deploy/docker-compose.yml down
```