#!/usr/bin/env bash
# ci.yml's gate: green only if every job succeeded. `engine-smoke` alone may be `skipped`, and only
# when the caller turned the live smoke off (LIVE_SMOKE=false: edge publishing and edge rollback,
# #37). Inputs are the jobs' results as env: LINT TYPES TEST DOCKER ENGINE_SMOKE, plus LIVE_SMOKE.
set -euo pipefail

: "${LIVE_SMOKE:=true}"
rows=("| job | result |" "|---|---|")
failed=()
for job in LINT TYPES TEST DOCKER ENGINE_SMOKE; do
  result="${!job:-missing}"
  name=$(echo "$job" | tr '[:upper:]_' '[:lower:]-')
  rows+=("| $name | $result |")
  if [ "$result" = "success" ]; then
    continue
  fi
  if [ "$job" = "ENGINE_SMOKE" ] && [ "$result" = "skipped" ] && [ "$LIVE_SMOKE" = "false" ]; then
    continue
  fi
  failed+=("$name=$result")
done
printf '%s\n' "${rows[@]}" "" "live smoke: $LIVE_SMOKE" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
if [ "${#failed[@]}" -gt 0 ]; then
  echo "gate: a job did not succeed (${failed[*]})"
  exit 1
fi
echo "gate: all jobs succeeded (live smoke: $LIVE_SMOKE)"
