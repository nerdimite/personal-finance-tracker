#!/usr/bin/env bash
# Nightly logical backup of the finance database.
# Writes a custom-format dump under backups/. Does not print secrets.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
mkdir -p backups

read_env_key() {
  local key="$1"
  local file="$root/.env"
  [[ -f "$file" ]] || return 0
  local line
  line="$(grep -E "^${key}=" "$file" | tail -n 1 || true)"
  if [[ -n "$line" ]]; then
    printf -v "$key" '%s' "${line#*=}"
    export "$key"
  fi
}

read_env_key POSTGRES_USER
read_env_key POSTGRES_DB

stamp="$(date +%Y%m%dT%H%M%S)"
out="backups/finance-${stamp}.dump"
docker compose exec -T postgres \
  pg_dump -U "${POSTGRES_USER:-owner}" -d "${POSTGRES_DB:-finance}" -Fc \
  > "$out"
echo "wrote ${out}"
