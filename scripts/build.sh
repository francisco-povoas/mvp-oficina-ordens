#!/bin/sh
set -eu
docker build --target runtime -t mvp-oficina-ordens:local .
