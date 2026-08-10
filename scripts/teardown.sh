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

tf_output() { $TF output -raw "$1" 2>/dev/null || true; }

# Capture what we need before the state is gone.
BUCKET="$(tf_output evidence_bucket)"
SECRET_ARN="$(tf_output secret_arn)"
FUNCTION="$(tf_output lambda_function_name)"

echo "Environment : ${ENVIRONMENT}"
echo "Function    : ${FUNCTION:-<none>}"
echo "Bucket      : ${BUCKET:-<none>}"
echo "Secret      : ${SECRET_ARN:-<none>}"
echo

echo "==> Planning destroy"
$TF plan -destroy -input=false \
  -var-file="${ENVIRONMENT}.tfvars" \
  -var="environment=${ENVIRONMENT}"

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
$TF destroy -input=false -auto-approve \
  -var-file="${ENVIRONMENT}.tfvars" \
  -var="environment=${ENVIRONMENT}"

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
