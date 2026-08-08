#!/usr/bin/env bash
# Publish a synthetic Grafana alert to the investigator's event bus.
#
# This exercises the real path — EventBridge rule matching, Lambda invocation,
# evidence collection, Bedrock, Slack — rather than invoking the function
# directly. The rule's event pattern is the piece most likely to be wrong, and
# a direct invoke would skip it entirely.
#
# Usage:
#   scripts/send_test_alert.sh                          # payment-api, critical
#   scripts/send_test_alert.sh checkout-api high
#   scripts/send_test_alert.sh payment-api critical resolved   # should be skipped
#
# Overrides: EVENT_BUS_NAME, EVENT_SOURCE, ECS_CLUSTER
set -euo pipefail

SERVICE="${1:-payment-api}"
SEVERITY="${2:-critical}"
STATUS="${3:-firing}"

for cmd in aws jq; do
  command -v "$cmd" >/dev/null || { echo "error: $cmd is required" >&2; exit 127; }
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

BUS="${EVENT_BUS_NAME:-}"
if [ -z "$BUS" ]; then
  BUS="$(terraform -chdir="$ROOT/terraform" output -raw event_bus_name 2>/dev/null || true)"
fi
if [ -z "$BUS" ]; then
  echo "error: could not determine the event bus name." >&2
  echo "       run where Terraform state is available, or set EVENT_BUS_NAME." >&2
  exit 1
fi

SOURCE="${EVENT_SOURCE:-grafana.alerting}"
CLUSTER="${ECS_CLUSTER:-}"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Mirrors Grafana's unified-alerting webhook shape (see tests/fixtures/).
detail="$(jq -n \
  --arg svc "$SERVICE" --arg sev "$SEVERITY" --arg status "$STATUS" \
  --arg now "$NOW" --arg cluster "$CLUSTER" '
  {
    receiver: "eventbridge",
    status: $status,
    title: ("[" + ($status | ascii_upcase) + ":1] SyntheticTestAlert " + $svc),
    commonLabels: ({ environment: "prod" } + (if $cluster == "" then {} else { cluster: $cluster } end)),
    alerts: [{
      status: $status,
      fingerprint: "synthetic-test-alert",
      startsAt: $now,
      valueString: ("[ var=B labels={service=" + $svc + "} value=8.42 ]"),
      labels: ({
        alertname: "SyntheticTestAlert",
        service: $svc,
        severity: $sev
      } + (if $cluster == "" then {} else { cluster: $cluster } end)),
      annotations: {
        summary: ($svc + " synthetic test alert from send_test_alert.sh"),
        description: "Manual pipeline test. Safe to ignore."
      }
    }]
  }')"

umask 077
entries="$(mktemp)"
trap 'rm -f "$entries"' EXIT

jq -n --arg bus "$BUS" --arg src "$SOURCE" --arg detail "$detail" \
  '[{ EventBusName: $bus, Source: $src, DetailType: "GrafanaAlert", Detail: $detail }]' > "$entries"

echo "Publishing to bus '${BUS}' (source '${SOURCE}'): ${SERVICE} / ${SEVERITY} / ${STATUS}"
result="$(aws events put-events --entries "file://$entries")"
echo "$result" | jq .

failed="$(echo "$result" | jq -r '.FailedEntryCount')"
if [ "$failed" != "0" ]; then
  echo "error: EventBridge rejected the event. Check the bus name and permissions." >&2
  exit 1
fi

cat <<'NEXT'

Published. EventBridge delivery is asynchronous — give it a few seconds, then:

  # Tail the investigation
  aws logs tail "$(terraform -chdir=terraform output -raw log_group_name)" --follow --since 5m

  # Anything that failed delivery entirely
  aws sqs get-queue-attributes \
    --queue-url "$(terraform -chdir=terraform output -raw dlq_url)" \
    --attribute-names ApproximateNumberOfMessages
NEXT
