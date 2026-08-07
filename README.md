# AI_Log_Investigator

“Ask one question. Get the evidence.”

the solution:
Grafana Alert

↓

EventBridge

↓

Lambda

↓

AI Log Investigator

↓

Collect Evidence

↓

Analyze

↓

Slack Summary

AWS Architecture:
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


Infrastructure (Terraform)

We’ll create reusable modules for:
terraform/

modules/

├── lambda
├── eventbridge
├── iam
├── secrets-manager
├── cloudwatch
├── s3
├── sns
├── bedrock

Lambda Responsibilities

The Lambda is the orchestrator.

1. Receive Alert

2. Identify Service

3. Query ECS

4. Query CloudWatch

5. Query Splunk

6. Build Prompt

7. Send to Bedrock

8. Receive AI Summary

9. Post to Slack


Splunk Investigation

Instead of querying everything, we’ll ask targeted questions.

Example:

Alert:
payment-api

HTTP 500

Started

10:35

Lambda builds Splunk searches such as:
index=prod

service=payment-api

earliest=-15m

latest=now

Then searches for:

* ERROR
* Exception
* Timeout
* OOMKilled
* Connection refused
* NullPointerException
* HTTP 500
* Database errors

Rather than sending raw logs to the LLM, we’ll extract and rank the most relevant evidence first. This “evidence reduction” approach is becoming a best practice because it dramatically reduces token usage while improving diagnosis quality.  

ECS Investigation

Using the ECS APIs we’ll collect:
DescribeServices

↓

Running Tasks

↓

Stopped Tasks

↓

Task Failures

↓

Recent Deployments

↓

Desired Count

↓

Running Count

↓

Pending Count
Example output:
Service

payment-api

Desired

6

Running

4

Pending

2

Deployment

Completed 8 min ago

Task

Restarting

CloudWatch Investigation

Automatically gather:

* CPU utilization
* Memory utilization
* ALB 5XX errors
* Request count
* Response time
* Container Insights metrics

AI Prompt

Instead of:
Analyze these logs.

We’ll use structured prompts like:

You are a Senior Site Reliability Engineer.

Your job is to investigate AWS ECS production incidents.

Alert

HTTP 500

Service

payment-api

ECS

Desired

6

Running

4

Recent Deployment

Yes

CloudWatch

CPU

28%

Memory

41%

Splunk

Top Errors

NullPointerException

Database timeout

Connection refused

Determine

1 Root Cause

2 Confidence

3 Evidence

4 Recommended Action

5 Business Impact

AI Response
Incident Summary

Likely Cause

Recent deployment introduced database connection exhaustion.

Confidence

91%

Evidence

Task restarted 12 times

Deployment 7 minutes ago

500 errors increased

Database timeout errors

Recommendations

Rollback deployment

Restart unhealthy tasks

Increase DB pool monitoring

Impact

Checkout service degraded

Slack Card
🚨 AI Incident Summary

Service

payment-api

Severity

Critical

Likely Cause

Database connection exhaustion

Confidence

91%

Evidence

• ECS task restarting

• 217 timeout errors

• Deployment 9 min ago

Recommended

Rollback deployment

Review DB connection pool

Restart unhealthy tasks

Python Package Structure
lambda/

app.py

collectors/

    ecs.py

    splunk.py

    cloudwatch.py

    grafana.py

ai/

    prompt.py

    bedrock.py

notifications/

    slack.py

utils/

    parser.py

    logger.py

    config.py

CI/CD Pipeline

We’ll deploy everything with GitHub Actions:
Push

↓

Terraform fmt

↓

Terraform validate

↓

Terraform plan

↓

Approval

↓

Terraform apply

↓

Build Lambda

↓

Zip

↓

Upload to S3

↓

Update Lambda

Future Enhancements

Once this MVP is working, we can add:

* AI-generated Splunk SPL from natural language (for example, “show all payment-api errors in the last 30 minutes”), an approach AWS has demonstrated with Bedrock and Splunk integrations.  
* Similarity search against previous incidents to suggest known fixes.
* Automatic runbook recommendations based on the diagnosed issue.
* Human-approved remediation actions such as restarting ECS tasks or rolling back deployments.
* Integration with Jira and PagerDuty for incident lifecycle management.

Recommended implementation order

1. Terraform infrastructure (Lambda, IAM, EventBridge, Secrets Manager, S3)
2. Lambda skeleton with structured logging
3. Splunk REST API integration
4. ECS and CloudWatch collectors
5. Amazon Bedrock integration
6. Slack notifications
7. GitHub Actions CI/CD
8. Automated tests with simulated production incidents

This project would be production-grade, demonstrate modern AI-assisted SRE practices, and make a strong portfolio piece for senior DevOps/SRE or Platform Engineering roles.

---

# Getting Started

## Repository layout

```
lambda/                  Application code (the deployment package root)
  app.py                 Handler: parse → collect → analyze → notify
  collectors/            ecs, cloudwatch, splunk, grafana
  ai/                    prompt construction + Bedrock inference
  notifications/         Slack Block Kit card
  utils/                 config, structured logging, alert parsing
terraform/               Root module + modules/{lambda,eventbridge,iam,
                         secrets-manager,cloudwatch,s3,sns,bedrock}
tests/                   pytest suite, incl. simulated production incidents
scripts/build_lambda.sh  Reproducible deployment-package build
.github/workflows/       ci.yml (lint/test/package), deploy.yml (plan→approve→apply)
```

## Local development

```bash
make install     # create .venv and install dev dependencies
make check       # ruff lint + format check + pytest
make build       # build build/lambda.zip
```

The build targets `manylinux2014_x86_64`, so the zip contains Linux binaries
and will not import on macOS — that is expected. CI verifies the import on
Linux.

## Deploying

```bash
cp terraform/terraform.tfvars.example terraform/terraform.tfvars   # edit
cp terraform/backend/prod.hcl.example terraform/backend/prod.hcl   # edit

make build
terraform -chdir=terraform init -backend-config=backend/prod.hcl
terraform -chdir=terraform plan -out=tfplan
terraform -chdir=terraform apply tfplan
```

`terraform output post_apply_checklist` lists the manual steps that remain
(populating the secret, confirming SNS subscriptions, pointing Grafana at the
event bus, and requesting Bedrock model access).

For CI deploys, set the repository variables `AWS_REGION`, `TF_STATE_BUCKET`,
`LAMBDA_ARTIFACT_BUCKET`, the secret `AWS_DEPLOY_ROLE_ARN` (OIDC), and commit a
non-sensitive `terraform/prod.tfvars`. The `production` GitHub Environment
provides the approval gate between `plan` and `apply`.

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
| `SECRET_ARN` | — | JSON secret: `splunk_token`, `slack_webhook_url`, `slack_bot_token`, `grafana_token` |
| `EVIDENCE_BUCKET` | — | Archives each investigation; empty disables |
| `DRY_RUN` | `false` | Build the Slack card without posting it |

## Failure behavior

The investigator degrades rather than going silent:

- A failing collector is recorded as `available: false` and the analysis
  continues with the remaining evidence — the model is told what is missing
  instead of reasoning as though the signal were negative.
- If Bedrock analysis fails, a degraded report carrying the raw evidence is
  still posted to Slack, and SNS notifies operators.
- A Slack delivery failure does not fail the invocation.
- An alert with no service label returns 400 (no retry — retrying cannot fix
  it); unexpected failures re-raise so EventBridge retries and the alert lands
  in the DLQ.
