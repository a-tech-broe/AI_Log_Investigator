# AI Log Investigator

> **"Ask one question. Get the evidence."**

A Grafana alert fires. Instead of paging a human to go read four consoles, an
AWS Lambda gathers the evidence from ECS, CloudWatch, and Splunk, hands a
structured summary to Claude on Amazon Bedrock, and posts a diagnosis to Slack
with a root cause, a calibrated confidence score, the evidence behind it, and
what to do next.

```
Grafana Alert → EventBridge → Lambda → Collect Evidence → Analyze → Slack Summary
```

---

## Architecture

```
                    Grafana Alert
                          │
                          ▼
                 Amazon EventBridge
                          │
                          ▼
                     Lambda Function
                          │
        ┌─────────────────┼─────────────────┐
        │                 │                 │
        ▼                 ▼                 ▼
    Splunk API      ECS Describe      CloudWatch
        │                 │                 │
        └─────────────────┼─────────────────┘
                          ▼
                 Evidence Collector
                          │
                          ▼
                 Amazon Bedrock
                          │
                          ▼
               AI Incident Report
                          │
                          ▼
                  Slack Notification
```

Collectors run concurrently — they hit independent, I/O-bound APIs, so the
investigation takes about as long as the slowest one rather than the sum.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.12+ | Lambda runtime is `python3.12` |
| Terraform ≥ 1.10 | Required for S3-native state locking (`use_lockfile`) |
| AWS CLI v2 | For bootstrap and manual invokes |
| Bedrock model access | Request access to `anthropic.claude-opus-5` in your region |
| An S3 bucket for Terraform state | Bootstrapped outside this stack; enable versioning |

No DynamoDB lock table is needed — the S3 backend locks via conditional writes.

---

## Quick start

```bash
make install     # create .venv and install dev dependencies
make check       # ruff lint + format check + pytest
make build       # build build/lambda.zip
```

The package targets `manylinux2014_x86_64`, so the zip contains Linux binaries
and **will not import on macOS** — that is expected, not a build failure. CI
verifies the import on Linux.

### Deploying

```bash
cp terraform/terraform.tfvars.example terraform/terraform.tfvars   # edit
cp terraform/backend/prod.hcl.example terraform/backend/prod.hcl   # edit

make build
terraform -chdir=terraform init -backend-config=backend/prod.hcl
terraform -chdir=terraform plan -out=tfplan
terraform -chdir=terraform apply tfplan
```

`terraform output post_apply_checklist` prints the manual steps that remain:

1. Populate the Secrets Manager secret with `splunk_token` and `slack_webhook_url`.
2. Confirm the SNS email subscriptions.
3. Point Grafana's contact point at the created event bus.
4. Request Bedrock model access if it isn't already granted.

---

## CI/CD

Two GitHub Actions workflows.

**`ci.yml`** — on every PR and push: ruff lint, format check, pytest with
coverage, Terraform fmt/validate, and a package build whose import is verified
on Linux.

**`deploy.yml`** — on push to `main` or manual dispatch, following the pipeline
below. The `production` GitHub Environment sits between plan and apply; give it
required reviewers so nothing applies unattended.

```
Push → fmt → validate → plan → approval → apply → build → zip → upload to S3 → update Lambda → smoke test
```

Apply consumes the exact plan file produced earlier, so it can never
re-plan into something the reviewer did not see.

Configure under **Settings → Secrets and variables → Actions**:

| Name | Kind | Purpose |
|---|---|---|
| `AWS_ACCESS_KEY_ID` | secret | Deploy credentials |
| `AWS_SECRET_ACCESS_KEY` | secret | Deploy credentials |
| `AWS_REGION` | secret or variable | Target region |
| `TF_STATE_BUCKET` | secret or variable | Terraform remote state bucket |
| `LAMBDA_ARTIFACT_BUCKET` | secret or variable | Deployment zip destination |

The last three are not sensitive, so either kind works — every reference reads
`secrets.X` and falls back to `vars.X`. Also commit a non-sensitive
`terraform/prod.tfvars` (copy the example).

The deploy identity needs permission to manage everything in `terraform/`
(Lambda, IAM, EventBridge, SQS, S3, SNS, Secrets Manager, CloudWatch) plus
read/write on the state and artifact buckets. Static access keys are long-lived
credentials — rotate them, and prefer OIDC role assumption if your account
allows it.

---

## How the investigation works

The Lambda is the orchestrator:

1. Receive alert → 2. Identify service → 3. Query ECS → 4. Query CloudWatch →
5. Query Splunk → 6. Build prompt → 7. Send to Bedrock → 8. Receive AI summary →
9. Post to Slack

### Alert parsing

Grafana's unified-alerting webhook is unwrapped from the EventBridge envelope.
The service label is the one thing an investigation cannot proceed without, so
it is looked up across the names teams actually use — `service`, `service_name`,
`app`, `application`, `ecs_service`, `job`, `container` — and severity is
normalized (`P1`/`sev1`/`crit` → `critical`). Only firing alerts are
investigated; resolved ones return early.

The evidence window is anchored on **when the alert fired**, not when the Lambda
runs, so a retry or cold start doesn't shift the search away from the incident.

### ECS investigation

`DescribeServices` → running tasks → stopped tasks → task failures → recent
deployments → desired/running/pending counts.

Because a Grafana label rarely matches the ECS service name exactly
(`payment-api` vs `payment-api-prod`), the collector falls back to a prefix
match. Stopped-task reasons are classified into `container_oom`,
`image_pull_failure`, `failed_health_checks`, `essential_container_exit`, and
similar, so the model gets a signal rather than a sentence to parse.

Example:

| Field | Value |
|---|---|
| Service | `payment-api` |
| Desired | 6 |
| Running | 4 |
| Pending | 2 |
| Deployment | Completed 8 min ago |
| Task | Restarting |

### CloudWatch investigation

Automatically gathered: CPU utilization, memory utilization, ALB 5XX errors,
request count, response time, healthy/unhealthy host counts, and Container
Insights metrics.

Each series is summarized (latest/min/max/avg/total) and a few ratios are
derived before the prompt is built — error rate, CPU and memory saturation
flags — so the model spends its reasoning on diagnosis rather than arithmetic.
Errors with flat CPU and memory is itself a strong signal: it points away from
capacity as the cause.

### Splunk investigation

Rather than querying everything, the collector asks a targeted question:

```
index=prod service=payment-api earliest=-15m latest=now
  (ERROR OR Exception OR Timeout OR OOMKilled OR "Connection refused" OR ...)
```

**Rather than sending raw logs to the LLM, evidence is extracted and ranked
first.** Each event is classified against ordered patterns — OOM kills,
connection-pool exhaustion, database errors, timeouts, null dereferences, DNS
failures, circuit breakers, rate limiting — then grouped, counted, and reduced
to the top patterns with two exemplars each.

Order matters: specific causes are matched before generic ones, so a line
reading `ERROR OOMKilled container terminated` is reported as an OOM kill rather
than a generic error. This evidence-reduction approach dramatically reduces
token usage while improving diagnosis quality.

### Grafana (optional)

Annotations in the alert window, with deploy and release markers called out
separately — useful corroboration when a rollout is the suspected trigger.

---

## The AI prompt

Instead of *"analyze these logs"*, the model receives a structured evidence
packet and a Senior SRE system prompt that tells it how to weigh the sources:
correlate across them, treat absence of evidence as information, and never read
a failed collector as a healthy signal.

Confidence is explicitly calibrated — 90+ only when the evidence is close to
conclusive, below 50 when it is largely a guess, because an accurate low number
is more useful to a responder than a confident wrong answer.

The response is constrained by a JSON schema, so the Slack renderer can rely on
every field existing:

| Field | Meaning |
|---|---|
| `root_cause` | The single most likely cause |
| `confidence` | 0–100, calibrated |
| `severity` | Derived from evidence, not the alert label |
| `category` | `deployment`, `resource_exhaustion`, `dependency_failure`, … |
| `evidence` | Observations, each traceable to the input |
| `recommendations` | Ordered `{action, rationale, urgency}` |
| `business_impact` | What users are experiencing, in plain language |
| `missing_evidence` | What was unavailable or would sharpen the diagnosis |

### Example output

```
Incident Summary

Likely Cause    Recent deployment introduced database connection exhaustion
Confidence      91%

Evidence        Task restarted 12 times
                Deployment 7 minutes ago
                500 errors increased
                Database timeout errors

Recommendations Rollback deployment
                Restart unhealthy tasks
                Increase DB pool monitoring

Impact          Checkout service degraded
```

### Slack card

```
🚨 AI Incident Summary

Service      payment-api          Severity   Critical
Alert        HighErrorRate        Category   Deployment

Likely Cause
Database connection exhaustion

Confidence
`█████████░` 91%

Evidence
• ECS task restarting
• 217 timeout errors
• Deployment 9 min ago

Recommended
⚡ Roll back to task definition payment-api:41
⏱️ Review DB connection pool
📋 Restart unhealthy tasks

Impact
Checkout is failing for roughly 7% of requests

[Open Dashboard] [Silence Alert]
Sources: ecs, cloudwatch, splunk · anthropic.claude-opus-5
```

---

## Repository layout

```
lambda/                    Application code (deployment package root)
  app.py                   Handler: parse → collect → analyze → notify
  collectors/              ecs, cloudwatch, splunk, grafana
  ai/                      prompt.py (schema + system prompt), bedrock.py
  notifications/slack.py   Block Kit card
  utils/                   config, structured logging, alert parsing
terraform/
  main.tf                  Root module wiring everything together
  modules/                 lambda, eventbridge, iam, secrets-manager,
                           cloudwatch, s3, sns, bedrock
tests/                     pytest suite, incl. simulated production incidents
scripts/build_lambda.sh    Reproducible deployment-package build
.github/workflows/         ci.yml, deploy.yml
```

---

## Configuration

Runtime behavior is environment-driven; Terraform sets these on the function.

| Variable | Default | Purpose |
|---|---|---|
| `ECS_CLUSTER` | — | Cluster used when the alert carries no cluster label |
| `LOOKBACK_MINUTES` | `15` | Evidence window before the alert fired |
| `SPLUNK_HOST` / `SPLUNK_INDEX` | — / `prod` | Splunk endpoint and index |
| `SPLUNK_MAX_EVENTS` | `500` | Events pulled before client-side reduction |
| `ALB_ARN_SUFFIX` / `TARGET_GROUP_ARN_SUFFIX` | — | Enable ALB metrics |
| `GRAFANA_URL` | — | Enables the annotation collector |
| `BEDROCK_MODEL_ID` | `anthropic.claude-opus-5` | Analysis model |
| `BEDROCK_EFFORT` | `high` | `low`…`max` — reasoning depth vs. cost |
| `BEDROCK_MAX_TOKENS` | `16000` | Output budget (thinking + report) |
| `SECRET_ARN` | — | JSON secret: `splunk_token`, `slack_webhook_url`, `slack_bot_token`, `grafana_token` |
| `EVIDENCE_BUCKET` | — | Archives each investigation; empty disables |
| `SNS_TOPIC_ARN` | — | Operator notifications on investigator failure |
| `DRY_RUN` | `false` | Build the Slack card without posting it |

### Security posture

The Lambda is **read-only against production**: it describes ECS and reads
metrics and logs, but holds no mutating permission on any investigated service.
Its only writes are its own log group, its evidence bucket prefix, and its ops
SNS topic. Third-party credentials live in one Secrets Manager secret that
Terraform creates but never populates — values are set out of band so they never
enter state or version control.

---

## Failure behavior

The investigator degrades rather than going silent:

- A failing collector is recorded as `available: false` and the analysis
  continues with the remaining evidence — the model is told what is missing
  instead of reasoning as though the signal were negative.
- If Bedrock analysis fails, a degraded report carrying the raw evidence is
  still posted to Slack, and SNS notifies operators.
- A Slack delivery failure does not fail the invocation.
- An alert with no service label returns 400 — retrying cannot fix it, so it is
  not retried. Unexpected failures re-raise so EventBridge retries and the alert
  lands in the DLQ rather than disappearing.

Alarms cover Lambda errors, throttles, duration approaching timeout, DLQ depth,
and — via a log metric filter — degraded reports, which would otherwise look
like success since the Lambda still returns 200.

---

## Testing

```bash
make test        # pytest
make coverage    # pytest with a coverage report
```

117 tests, 98% coverage. Every external boundary (ECS, CloudWatch, Splunk,
Grafana, Bedrock, Slack, S3, SNS, Secrets Manager) is stubbed, so the suite
needs no AWS credentials and no network.

Coverage includes simulated production incidents end-to-end — a bad deployment
exhausting a connection pool, and tasks being OOM-killed with no deployment
involved — plus the degradation paths: analysis failure, Slack failure, and
total collector failure.

---

## Future enhancements

* AI-generated Splunk SPL from natural language (for example, "show all
  payment-api errors in the last 30 minutes"), an approach AWS has demonstrated
  with Bedrock and Splunk integrations.
* Similarity search against previous incidents to suggest known fixes.
* Automatic runbook recommendations based on the diagnosed issue.
* Human-approved remediation actions such as restarting ECS tasks or rolling
  back deployments.
* Integration with Jira and PagerDuty for incident lifecycle management.
* Alert-storm deduplication (a DynamoDB table keyed on service + fingerprint
  with a short TTL) so one bad deploy produces one investigation rather than N.

## Implementation status

1. ✅ Terraform infrastructure (Lambda, IAM, EventBridge, Secrets Manager, S3)
2. ✅ Lambda skeleton with structured logging
3. ✅ Splunk REST API integration
4. ✅ ECS and CloudWatch collectors
5. ✅ Amazon Bedrock integration
6. ✅ Slack notifications
7. ✅ GitHub Actions CI/CD
8. ✅ Automated tests with simulated production incidents
