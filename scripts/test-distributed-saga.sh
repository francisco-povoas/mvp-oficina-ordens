#!/bin/sh

set -eu

compose_file="compose.saga.test.yaml"

cleanup() {
  docker compose -f "$compose_file" down --volumes --remove-orphans
}

trap cleanup EXIT INT TERM

docker compose -f "$compose_file" build
docker compose -f "$compose_file" up -d --wait ordens-db recursos-db elasticmq dynamodb
docker compose -f "$compose_file" run --rm --no-deps ordens-migrate
docker compose -f "$compose_file" run --rm --no-deps recursos-migrate
docker compose -f "$compose_file" run --rm --no-deps financeiro-bootstrap
docker compose -f "$compose_file" up -d --no-deps \
  ordens-api financeiro-api ordens-publisher ordens-consumer \
  recursos-consumer recursos-publisher financeiro-publisher
docker compose -f "$compose_file" run --rm --no-deps saga-tests
