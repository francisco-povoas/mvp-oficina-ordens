#!/bin/sh

set -eu

compose_file="compose.saga.test.yaml"

cleanup() {
  docker compose -f "$compose_file" down --volumes --remove-orphans
}

trap cleanup EXIT INT TERM

docker compose -f "$compose_file" build
docker compose -f "$compose_file" up -d --wait ordens-db recursos-db elasticmq
docker compose -f "$compose_file" run --rm --no-deps ordens-migrate
docker compose -f "$compose_file" run --rm --no-deps recursos-migrate
docker compose -f "$compose_file" up -d --no-deps \
  ordens-publisher ordens-consumer recursos-consumer recursos-publisher
docker compose -f "$compose_file" run --rm --no-deps saga-tests
