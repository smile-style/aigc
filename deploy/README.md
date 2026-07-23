# Deployment

The deployment directory has three independent areas:

- `build/`: Docker image build files and build instructions.
- `prod/`: master production Compose configuration and production data paths.
- `pre/`: codex pre-release Compose configuration and isolated pre-release data paths.

Each runtime directory owns its `.env`, `db.sqlite3`, `media/`, and
`workspace/`. Real environment files and runtime data are ignored by Git.

## First-time setup


## Move existing production data

Stop the old production services before copying SQLite data. From the repository
root, keep the old files as a backup and copy them into the new production
directory:

```bash
cp -a /data/aigc_data/db.sqlite3 deploy/prod/db.sqlite3
rsync -a /data/aigc_data/media/ deploy/prod/media/
rsync -a /data/aigc_data/workspace/ deploy/prod/workspace/
```

Move the old `deploy/.env` to `deploy/prod/.env`. Review `AIGC_IMAGE`,
`AIGC_HTTP_PORT`, allowed hosts, and trusted origins before starting. Do not
copy a live SQLite database; stop all old web and worker containers first.

Create the runtime files before starting Compose:

```bash
cp deploy/prod/.env.example deploy/prod/.env
touch deploy/prod/db.sqlite3

cp deploy/pre/.env.example deploy/pre/.env
touch deploy/pre/db.sqlite3
```

Keep existing production data under `deploy/prod/`. Do not copy production
`db.sqlite3` or media directly into `deploy/pre/` while production is running.

## Start production

```bash
docker compose --env-file deploy/prod/.env \
  -f deploy/prod/docker-compose.yaml config
docker compose --env-file deploy/prod/.env \
  -f deploy/prod/docker-compose.yaml up -d
```

Production defaults to port `8080`.

## Start pre-release

```bash
docker compose --env-file deploy/pre/.env \
  -f deploy/pre/docker-compose.yaml config
docker compose --env-file deploy/pre/.env \
  -f deploy/pre/docker-compose.yaml up -d
```

Pre-release defaults to port `8081`. The fixed Compose project names
`aigc-prod` and `aigc-pre` prevent cross-environment container operations.

Run `down` with the matching Compose file. Never delete or overwrite
`db.sqlite3`, `media/`, or `workspace/` without a verified backup.
