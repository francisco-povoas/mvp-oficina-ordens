#!/bin/sh
set -eu

alembic upgrade head
python -m app.bootstrap_iam
exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8001
