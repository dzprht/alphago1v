#!/bin/sh
set -eu

revision=${1:?Pass the tested commit SHA}
test "$(git rev-parse HEAD)" = "$revision"
if [ "$(git rev-parse origin/main)" != "$revision" ]; then
    echo "A newer commit is on main skipping this deployment"
    exit 0
fi
export APP_ENV_FILE=${APP_ENV_FILE:-.env}

docker compose --env-file "$APP_ENV_FILE" -f compose.prod.yaml build app
docker compose --env-file "$APP_ENV_FILE" -f compose.prod.yaml up -d --wait --wait-timeout 90 db
docker compose --env-file "$APP_ENV_FILE" -f compose.prod.yaml run --rm --interactive=false --no-deps app python manage.py check_models
docker compose --env-file "$APP_ENV_FILE" -f compose.prod.yaml run --rm --interactive=false --no-deps app python manage.py migrate --noinput
docker compose --env-file "$APP_ENV_FILE" -f compose.prod.yaml up -d --wait --wait-timeout 300 app
echo "Deployed $revision"
