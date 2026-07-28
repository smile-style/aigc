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

## Optional MySQL database

SQLite remains the default. To use the bundled MySQL service, create its data
directory, set strong database passwords in the environment's `.env`, and
enable the Compose profile and Django database engine:

```bash
mkdir -p /data/aigc_data/pre/mysql/data
# Or for production: mkdir -p /data/aigc_data/prod/mysql/data
```

```dotenv
DB_ENGINE=mysql
COMPOSE_PROFILES=mysql
MYSQL_DATABASE=AIGC_STUDIO
MYSQL_USER=aigc_studio
MYSQL_PASSWORD=replace-with-a-strong-database-password
MYSQL_ROOT_PASSWORD=replace-with-a-strong-root-password
```

Then run the normal `docker compose config`, `pull`, and `up -d` commands from
the matching runtime directory. MySQL is only reachable on the Compose network;
port `3306` is not exposed on the host.

Existing SQLite data is not migrated automatically. Do not switch `DB_ENGINE`
to `mysql` in pre-release or production until that data has been backed up and
migrated to MySQL.

Run `down` with the matching Compose file. Never delete or overwrite
`db.sqlite3`, `media/`, or `workspace/` without a verified backup.
## Speech-aligned subtitles

Runtime Compose files enable `faster-whisper` with the CPU `small` model and
`int8` compute by default. The first subtitle alignment downloads the model to
`/app/workspace/.cache/huggingface`; this path is persisted by the workspace
volume, so later jobs and container restarts reuse the same model files.

`SUBTITLE_MAX_TIMELINE_DRIFT_MS` controls the maximum cumulative difference
between source clips and normalized 25fps clips. Captioned export fails with a
diagnostic message when that limit is exceeded. Per-cue overflow is controlled
by `SUBTITLE_CUE_BOUNDARY_TOLERANCE_MS`.
