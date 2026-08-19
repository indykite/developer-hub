#!/usr/bin/env bash
# Switch the demo usecase: point .env at the usecase's complete env file
# (bindings, secrets, USECASE var) and recreate whatever changed.
#   ./switch-usecase.sh canbank|insurance
set -euo pipefail
cd "$(dirname "$0")"
available=$(for f in .env.*; do [[ -e "${f}" ]] && printf '%s ' "${f#.env.}"; done)
name="${1:?usage: ./switch-usecase.sh <usecase>  (one of: ${available:-none})}"
if [[ ! -f ".env.${name}" ]]; then
    echo "no .env.${name} - create it first (see usecases/README.md)"
    exit 1
fi
if grep -q "restore before switching" ".env.${name}"; then
    echo "!! .env.${name} still has placeholder values - fill them first"
    exit 1
fi
ln -sf ".env.${name}" .env
echo "active usecase: ${name}"
docker compose up -d
