# Deployment

The deployment directory has three independent areas:

- `build/`: Docker image build files and build instructions.
- `prod/`: production environment and production data paths.
- `pre/`: pre-release environment and isolated pre-release data paths.

Each runtime directory owns its `.env`, `db.sqlite3`, `media/`, and
`workspace/`. Real environment files and runtime data are ignored by Git.
The two environments use identical Compose files and the same `AIGC_IMAGE`.
Their directory names, ports, and relative bind mounts keep runtime state
isolated.

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

## First-time setup

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
