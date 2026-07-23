# Deployment

The deployment directory has three independent areas:

- `build/`: Docker image build files and build instructions.
- `prod/`: production environment and production data paths.
- `pre/`: pre-release environment and isolated pre-release data paths.

Each runtime directory owns its `.env`, while runtime data is stored under
`/data/aigc_data/prod` and `/data/aigc_data/pre`. The two environments use the
same `AIGC_IMAGE`; their ports and absolute bind mounts keep runtime state
isolated.

## Move existing production data

Stop the old production services before copying SQLite data. From the repository
root, keep the old files as a backup and copy them into the new production
directory:

```bash
mkdir -p /data/aigc_data/prod
cp -a /data/aigc_data/db.sqlite3 /data/aigc_data/prod/db.sqlite3
rsync -a /data/aigc_data/media/ /data/aigc_data/prod/media/
rsync -a /data/aigc_data/workspace/ /data/aigc_data/prod/workspace/
```

Move the old `deploy/.env` to `deploy/prod/.env`. Review `AIGC_IMAGE`,
`AIGC_HTTP_PORT`, allowed hosts, and trusted origins before starting. Do not
copy a live SQLite database; stop all old web and worker containers first.

## First-time setup

Create the runtime files before starting Compose:

```bash
cp deploy/prod/.env.example deploy/prod/.env
mkdir -p /data/aigc_data/prod/media /data/aigc_data/prod/workspace
touch /data/aigc_data/prod/db.sqlite3

cp deploy/pre/.env.example deploy/pre/.env
mkdir -p /data/aigc_data/pre/media /data/aigc_data/pre/workspace
touch /data/aigc_data/pre/db.sqlite3
```

Keep existing production data under `/data/aigc_data/prod`. Do not copy
production `db.sqlite3` or media directly into `/data/aigc_data/pre` while
production is running.

## Start production

```bash
cd deploy/prod
docker compose config
docker compose pull
docker compose up -d
```

Production defaults to port `8080`.

## Start pre-release

```bash
cd deploy/pre
docker compose config
docker compose pull
docker compose up -d
```

Pre-release defaults to port `8081`. Compose derives different project names
from the `prod` and `pre` directory names.

Run `down` with the matching Compose file. Never delete or overwrite
`db.sqlite3`, `media/`, or `workspace/` without a verified backup.
