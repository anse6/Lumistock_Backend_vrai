#!/usr/bin/env bash
set -euo pipefail

# Les anciens scripts MongoDB ont été remplacés par une migration SQL
# idempotente qui crée les tables et leurs index SQL.
PYTHON_BIN="${PYTHON_BIN:-python3}"
"${PYTHON_BIN}" -m scripts.migrate_database
