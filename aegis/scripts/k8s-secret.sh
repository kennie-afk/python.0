#!/usr/bin/env bash
# Prints a Secret manifest with freshly generated random values to stdout. Nothing is written to disk:
#   ./scripts/k8s-secret.sh | kubectl apply -f -
# Run it once. Running it again would replace the database passwords with ones the existing database does not have.
# Hex keeps every value inside the characters Aegis accepts for the role password (no quotes).
# AEGIS_ANONYMISATION_SALT pins every pseudonym: never rotate it once data exists.
set -euo pipefail
rand() { openssl rand -hex "$1"; }
cat <<YAML
apiVersion: v1
kind: Secret
metadata:
  name: aegis-secrets
  namespace: aegis
type: Opaque
stringData:
  AEGIS_DB_OWNER_PASSWORD: $(rand 24)
  AEGIS_APP_PASSWORD: $(rand 24)
  AEGIS_JWT_SECRET: $(rand 48)
  AEGIS_ANONYMISATION_SALT: $(rand 32)
  AEGIS_LEDGER_SIGNING_KEY: $(rand 32)
YAML
