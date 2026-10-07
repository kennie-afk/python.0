#!/bin/sh
# Container entrypoint. With AEGIS_RUN_MIGRATIONS=true (the default, so a single `docker run` against
# an empty database still works) it applies migrations first. In compose and Kubernetes set it to
# false and run `alembic upgrade head` once, as a one-shot service or a Job, so replicas do not all
# migrate on every start. Migrations take a database advisory lock, so concurrent runs are safe
# either way; they just stop being the replicas' job.
set -eu
if [ "${AEGIS_RUN_MIGRATIONS:-true}" = "true" ]; then
    alembic upgrade head
fi
exec "$@"
