#!/usr/bin/env bash
# Set one key in the integrations secret without clobbering the others.
#
# The Lambda reads a single JSON secret holding every third-party credential,
# so a plain `put-secret-value` with one key silently deletes the rest. This
# fetches the current value, merges the key in, and writes it back.
#
# Usage:
#   scripts/set_secret.sh slack_webhook_url            # prompts for the value
#   scripts/set_secret.sh slack_webhook_url 'https://hooks.slack.com/...'
#
# Valid keys: splunk_token, slack_webhook_url, slack_bot_token, grafana_token
#
# Prefer the prompt form: a value passed as an argument lands in your shell
# history and is briefly visible to other users via `ps`.
set -euo pipefail

KEY="${1:-}"
VALUE="${2:-}"

if [ -z "$KEY" ]; then
  echo "usage: $0 <key> [value]" >&2
  exit 64
fi

case "$KEY" in
  splunk_token | slack_webhook_url | slack_bot_token | grafana_token) ;;
  *)
    echo "error: unknown key '$KEY'." >&2
    echo "       expected one of: splunk_token, slack_webhook_url, slack_bot_token, grafana_token" >&2
    exit 64
    ;;
esac

for cmd in aws jq; do
  command -v "$cmd" >/dev/null || { echo "error: $cmd is required" >&2; exit 127; }
done

# Resolve the secret from the environment, else from Terraform state.
SECRET_ARN="${SECRET_ARN:-}"
if [ -z "$SECRET_ARN" ]; then
  ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  SECRET_ARN="$(terraform -chdir="$ROOT/terraform" output -raw secret_arn 2>/dev/null || true)"
fi
if [ -z "$SECRET_ARN" ]; then
  echo "error: could not determine the secret ARN." >&2
  echo "       run from a directory with Terraform state, or set SECRET_ARN." >&2
  exit 1
fi

if [ -z "$VALUE" ]; then
  read -rsp "Value for ${KEY}: " VALUE
  echo
fi
[ -n "$VALUE" ] || { echo "error: empty value" >&2; exit 64; }

# Restrictive perms and a trap: the plaintext secret touches disk here.
umask 077
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

CURRENT="$(aws secretsmanager get-secret-value \
  --secret-id "$SECRET_ARN" --query SecretString --output text 2>/dev/null || echo '{}')"

# A freshly-seeded secret may hold placeholders; treat non-JSON as empty.
echo "$CURRENT" | jq empty >/dev/null 2>&1 || CURRENT='{}'

echo "$CURRENT" | jq --arg k "$KEY" --arg v "$VALUE" '.[$k] = $v' > "$TMP"

aws secretsmanager put-secret-value \
  --secret-id "$SECRET_ARN" \
  --secret-string "file://$TMP" >/dev/null

echo "Set '${KEY}'. Keys now present: $(jq -r 'keys | join(", ")' "$TMP")"
echo
echo "Note: the Lambda caches secrets for the life of its container. A warm"
echo "container keeps the old value until it recycles — force a cold start with:"
echo "  aws lambda update-function-configuration --function-name <name> \\"
echo "    --description \"secret rotated \$(date -u +%FT%TZ)\""
