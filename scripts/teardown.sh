#!/usr/bin/env bash
# Tear down a deployed environment.
#
# `terraform destroy` alone is not enough here: the evidence bucket is versioned
# with force_destroy = false, so Terraform fails with BucketNotEmpty and leaves
# the stack half-destroyed. This empties the bucket first (every version and
# delete marker), then destroys, then optionally purges the secret so the name
# is free for an immediate redeploy.
#
# Usage:
#   scripts/teardown.sh                 # prod, plan then confirm
#   scripts/teardown.sh staging
#   scripts/teardown.sh prod --plan     # show what would go, change nothing
#
# Environment:
#   AUTO_APPROVE=true   skip the typed confirmation (for CI)
#   KEEP_EVIDENCE=true  refuse to empty the bucket; destroy will fail if it has objects
#   PURGE_SECRET=true   delete the secret immediately instead of a 7-day recovery window
set -euo pipefail

ENVIRONMENT="${1:-prod}"
MODE="${2:-}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF="terraform -chdir=$ROOT/terraform"

for cmd in aws jq terraform; do
  command -v "$cmd" >/dev/null || { echo "error: $cmd is required" >&2; exit 127; }
done

if ! $TF providers >/dev/null 2>&1; then
  echo "error: Terraform is not initialized for this environment." >&2
  echo "       run: terraform -chdir=terraform init -backend-config=backend/${ENVIRONMENT}.hcl" >&2
  exit 1
fi

# Nothing in state means this environment was never deployed (or is already
# gone). That is success, not failure — exit cleanly so CI stays green.
RESOURCE_COUNT="$($TF state list 2>/dev/null | grep -c . || true)"
if [ "${RESOURCE_COUNT:-0}" -eq 0 ]; then
  echo "Nothing to tear down: no resources in state for '${ENVIRONMENT}'."
  exit 0
fi

# Read outputs as JSON. `output -raw` prints its "No outputs found" warning on
# stdout, which would otherwise be captured as the value itself.
OUTPUTS="$($TF output -json 2>/dev/null || echo '{}')"
echo "$OUTPUTS" | jq empty >/dev/null 2>&1 || OUTPUTS='{}'
tf_output() { echo "$OUTPUTS" | jq -r --arg k "$1" '.[$k].value // empty'; }

# Capture what we need before the state is gone.
BUCKET="$(tf_output evidence_bucket)"
SECRET_ARN="$(tf_output secret_arn)"
FUNCTION="$(tf_output lambda_function_name)"

# Variable values do not affect what gets destroyed — Terraform works from
# state — but the config must still evaluate, and ecs_cluster_name has no
# default. Use the environment's tfvars when present, else a placeholder.
VAR_ARGS=(-var="environment=${ENVIRONMENT}")
if [ -f "$ROOT/terraform/${ENVIRONMENT}.tfvars" ]; then
  VAR_ARGS=(-var-file="${ENVIRONMENT}.tfvars" "${VAR_ARGS[@]}")
else
  echo "note: terraform/${ENVIRONMENT}.tfvars not found; supplying placeholders."
  VAR_ARGS+=(-var="ecs_cluster_name=unused-during-destroy")
fi

echo "Environment : ${ENVIRONMENT}"
echo "Resources   : ${RESOURCE_COUNT} in state"
echo "Function    : ${FUNCTION:-<none>}"
echo "Bucket      : ${BUCKET:-<none>}"
echo "Secret      : ${SECRET_ARN:-<none>}"
echo

echo "==> Planning destroy"
$TF plan -destroy -input=false "${VAR_ARGS[@]}"

if [ "$MODE" = "--plan" ]; then
  echo
  echo "Plan only — nothing was changed."
  exit 0
fi

if [ "${AUTO_APPROVE:-}" != "true" ]; then
  echo
  echo "This permanently deletes the stack above, including archived investigations."
  printf "Type the environment name (%s) to continue: " "$ENVIRONMENT"
  read -r reply
  if [ "$reply" != "$ENVIRONMENT" ]; then
    echo "Aborted — input did not match." >&2
    exit 1
  fi
fi

# --- Empty the versioned evidence bucket -------------------------------------

empty_bucket() {
  local bucket="$1" tmp del count total=0
  tmp="$(mktemp)"; del="$(mktemp)"
  trap 'rm -f "$tmp" "$del"' RETURN

  if ! aws s3api head-bucket --bucket "$bucket" >/dev/null 2>&1; then
    echo "    bucket does not exist or is not accessible; skipping"
    return 0
  fi

  while :; do
    aws s3api list-object-versions --bucket "$bucket" --max-keys 1000 --output json > "$tmp" 2>/dev/null || break

    # Versions and delete markers both keep a bucket from being deletable.
    jq '{
      Objects: [ (.Versions // [])[], (.DeleteMarkers // [])[] ]
                 | map({Key: .Key, VersionId: .VersionId}),
      Quiet: true
    }' "$tmp" > "$del"

    count="$(jq '.Objects | length' "$del")"
    [ "$count" -eq 0 ] && break

    aws s3api delete-objects --bucket "$bucket" --delete "file://$del" >/dev/null
    total=$((total + count))
    echo "    deleted ${total} object versions…"
  done

  echo "    bucket emptied (${total} object versions removed)"
}

if [ -n "$BUCKET" ]; then
  if [ "${KEEP_EVIDENCE:-}" = "true" ]; then
    echo "==> KEEP_EVIDENCE=true — leaving ${BUCKET} intact (destroy will fail if it has objects)"
  else
    echo "==> Emptying evidence bucket ${BUCKET}"
    empty_bucket "$BUCKET"
  fi
fi

# --- Destroy ------------------------------------------------------------------

echo "==> Destroying"
$TF destroy -input=false -auto-approve "${VAR_ARGS[@]}"

# --- Post-destroy -------------------------------------------------------------

# Terraform schedules the secret for deletion with a recovery window, which
# reserves the name. Redeploying inside that window fails.
if [ -n "$SECRET_ARN" ] && [ "${PURGE_SECRET:-}" = "true" ]; then
  echo "==> Purging secret (no recovery window)"
  aws secretsmanager delete-secret \
    --secret-id "$SECRET_ARN" \
    --force-delete-without-recovery >/dev/null 2>&1 \
    && echo "    purged" \
    || echo "    already gone"
fi

cat <<'DONE'

==> Teardown complete.

Not managed by this stack, so still present if you created them:
  - the Terraform state bucket
  - the Lambda artifact bucket (deployment zips under lambda/<env>/)
  - any Bedrock model access you requested

If you did not pass PURGE_SECRET=true, the secret name stays reserved for its
recovery window; a redeploy before it lapses will fail. Purge it with:
  aws secretsmanager delete-secret --secret-id <arn> --force-delete-without-recovery
DONE
