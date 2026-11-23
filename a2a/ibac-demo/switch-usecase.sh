#!/usr/bin/env bash
# Switch the demo usecase: point .env at the usecase's complete env file
# (bindings, secrets, USECASE var) and recreate whatever changed.
#   ./switch-usecase.sh ibac
set -euo pipefail
cd "$(dirname "$0")"
available=$(for f in .env.*; do if [[ -e "${f}" ]]; then printf '%s ' "${f#.env.}"; fi; done)
name="${1:?usage: ./switch-usecase.sh <usecase>  (one of: ${available:-none})}"
if [[ ! -f ".env.${name}" ]]; then
    echo "no .env.${name} - create it first (see usecases/README.md)"
    exit 1
fi
if [[ ! -d "usecases/${name}" ]]; then
    echo "no usecases/${name}/ bundle - see usecases/README.md"
    exit 1
fi
if grep -q "restore before switching" ".env.${name}"; then
    echo "!! .env.${name} still has placeholder values - fill them first"
    exit 1
fi
# The env file names its own bundle: a file copied from another usecase and
# not adapted would start that usecase's skills against this one's project.
declared=$(grep -E '^USECASE=' ".env.${name}" | tail -n 1 | cut -d= -f2- | tr -d "\"' \r" || true)
if [[ "${declared}" != "${name}" ]]; then
    echo "!! .env.${name} sets USECASE=${declared:-<nothing>} - it must be USECASE=${name}"
    exit 1
fi
# A plain .env file is a usecase's only copy of its secrets: never replace it.
if [[ -e .env && ! -L .env ]]; then
    echo "!! .env is a regular file - move it first: mv .env .env.<usecase>"
    exit 1
fi
ln -sfn ".env.${name}" .env
echo "active usecase: ${name}"
# --remove-orphans stops containers of profile-gated services (drive, crm,
# erp, intent) that the new usecase's COMPOSE_PROFILES no longer enables.
docker compose up -d --remove-orphans
