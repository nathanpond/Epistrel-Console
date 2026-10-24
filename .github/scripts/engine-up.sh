#!/usr/bin/env bash
# Start the Engine image next to a Postgres service container, wait for it, and say which build it
# is. Used by ci.yml's engine-smoke job (#36) and engine-drift.yml (#40).
#
#   engine-up.sh start      pull ENGINE_IMAGE (one retry) and `docker run` it as `engine` on NETWORK
#   engine-up.sh wait       poll http://127.0.0.1:8000/health every 2 s, up to 120 s
#   engine-up.sh identify   write engine_digest / engine_version to $GITHUB_OUTPUT
#   engine-up.sh summary    a two-row table in $GITHUB_STEP_SUMMARY (ENGINE_DIGEST, ENGINE_VERSION)
#   engine-up.sh logs       dump the Engine's and Postgres's logs (POSTGRES_CONTAINER)
#
# The Engine is a step, not a service container: it runs its migrations at start and exits if
# Postgres is not ready yet, and service containers start together. An Engine refuses EPISTREL_*
# variables it does not know, and `edge` refuses to start without a service credential. Released
# Engines know fewer settings than `edge` (0.1.x has no SERVICE_CREDENTIALS or CONFIG_KEY, #124).
# So `start` asks the image which settings it has (the Settings model, via the image's own Python)
# and passes only those of ENGINE_SETTINGS. It refuses to guess when the probe fails or the image
# lacks DATABASE_URL. The credential and config key are the Engine compose's dev-only literals (as
# in docker-compose.yml); the smoke calls only unauthenticated routes.
set -euo pipefail

ENGINE_URL=http://127.0.0.1:8000
ENGINE_REPO=ghcr.io/nathanpond/epistrel
WAIT_S=120

# Every setting the smoke may pass; `start` drops the ones the image does not know.
ENGINE_SETTINGS=(
  "EPISTREL_DATABASE_URL=postgresql+psycopg://epistrel:epistrel-dev@postgres:5432/epistrel"
  "EPISTREL_LOG_LEVEL=INFO"
  "EPISTREL_SERVICE_CREDENTIALS=dev-only-credential-change-me-00000000"
  "EPISTREL_CONFIG_KEY=ZXBpc3RyZWwtZGV2LW9ubHktY29uZmlnLWtleS0wMDA="
)
PROBE='from epistrel.config import ENV_PREFIX, Settings
print("\n".join(ENV_PREFIX + name.upper() for name in Settings.model_fields))'

# The EPISTREL_* names ENGINE_IMAGE's Settings model has, one per line.
known_settings() {
  docker run --rm --entrypoint python "$ENGINE_IMAGE" -c "$PROBE"
}

start() {
  : "${ENGINE_IMAGE:?ENGINE_IMAGE is required}" "${NETWORK:?NETWORK is required}"
  docker pull "$ENGINE_IMAGE" || { echo "::warning::pull failed; retrying in 10 s"; sleep 10; docker pull "$ENGINE_IMAGE"; }
  local known setting args=()
  if ! known=$(known_settings); then
    echo "::error::could not read the settings $ENGINE_IMAGE accepts"
    return 1
  fi
  if ! grep -qx 'EPISTREL_DATABASE_URL' <<<"$known"; then
    echo "::error::$ENGINE_IMAGE has no EPISTREL_DATABASE_URL setting; got: $(tr '\n' ' ' <<<"$known")"
    return 1
  fi
  for setting in "${ENGINE_SETTINGS[@]}"; do
    if grep -qx "${setting%%=*}" <<<"$known"; then
      args+=(-e "$setting")
    else
      echo "$ENGINE_IMAGE has no ${setting%%=*}; not passed"
    fi
  done
  docker run -d --name engine --network "$NETWORK" -p 127.0.0.1:8000:8000 "${args[@]}" "$ENGINE_IMAGE"
}

wait_healthy() {
  local deadline=$((SECONDS + WAIT_S))
  while [ "$SECONDS" -lt "$deadline" ]; do
    if curl -fsS --max-time 5 "$ENGINE_URL/health" >/dev/null 2>&1; then
      echo "Engine healthy after $((SECONDS - deadline + WAIT_S)) s"
      return 0
    fi
    sleep 2
  done
  logs
  echo "::error::Engine ${ENGINE_IMAGE:-} did not become healthy in $WAIT_S s"
  return 1
}

identify() {
  local image digest version
  image=$(docker inspect engine --format '{{.Image}}')
  digest=$(docker image inspect "$image" --format '{{range .RepoDigests}}{{println .}}{{end}}' \
    | grep "^$ENGINE_REPO@" | head -n 1 || true)
  if [ -z "$digest" ]; then
    echo "::error::no $ENGINE_REPO@sha256 digest for the running Engine image"
    return 1
  fi
  version=$(curl -fsS --max-time 10 "$ENGINE_URL/openapi.json" \
    | python3 -c 'import json,sys; v=json.load(sys.stdin).get("info",{}).get("version"); print(v if isinstance(v,str) and v else "unknown")' \
    || echo unknown)
  echo "Engine $version ($digest)"
  {
    echo "engine_digest=$digest"
    echo "engine_version=$version"
  } >> "${GITHUB_OUTPUT:-/dev/null}"
}

summary() {
  printf '%s\n' '| Engine | |' '|---|---|' \
    "| image | \`${ENGINE_DIGEST:-unknown}\` |" \
    "| version | \`${ENGINE_VERSION:-unknown}\` |" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
}

logs() {
  echo "::group::Engine logs"
  docker logs engine 2>&1 | tail -n 200 || true
  echo "::endgroup::"
  if [ -n "${POSTGRES_CONTAINER:-}" ]; then
    echo "::group::Postgres logs"
    docker logs "$POSTGRES_CONTAINER" 2>&1 | tail -n 100 || true
    echo "::endgroup::"
  fi
}

case "${1:-}" in
  start) start ;;
  wait) wait_healthy ;;
  identify) identify ;;
  summary) summary ;;
  logs) logs ;;
  *) echo "usage: $0 start|wait|identify|summary|logs" >&2; exit 2 ;;
esac
