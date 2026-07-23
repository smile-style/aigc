# Image build

All image build files live in this directory. Run commands from the repository root.

```bash
cp deploy/build/.env.example deploy/build/.env

docker compose --env-file deploy/build/.env \
  -f deploy/build/docker-compose.yaml build --pull aigc-base
docker compose --env-file deploy/build/.env \
  -f deploy/build/docker-compose.yaml push aigc-base

docker compose --env-file deploy/build/.env \
  -f deploy/build/docker-compose.yaml build --pull aigc-studio-image
docker compose --env-file deploy/build/.env \
  -f deploy/build/docker-compose.yaml push aigc-studio-image
```

Rebuild the base image only when Python or operating-system packages change.
For application code or Python dependency changes, rebuild only `aigc-studio-image`.

Set the exact same `AIGC_IMAGE` value in `deploy/prod/.env` and
`deploy/pre/.env`. Build and push the application image once; both environments
then pull that image.
