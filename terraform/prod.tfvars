# Non-sensitive configuration for the `prod` environment. Safe to commit.
# Credentials belong in Secrets Manager — never in this file.
#
# `aws_region` and `environment` are supplied by the deploy workflow, so they
# are deliberately not set here.

# ---- Required ---------------------------------------------------------------

# EDIT ME: the ECS cluster investigated when an alert carries no cluster label.
ecs_cluster_name = "prod-ecs"

# ---- Optional ---------------------------------------------------------------
# Defaults shown; uncomment only what you need to change.

# Scope IAM to specific clusters. Empty (the default) allows account-wide ECS
# reads, which is the right starting point if you have one cluster.
# ecs_cluster_arns = ["arn:aws:ecs:us-east-1:123456789012:cluster/prod-ecs"]

# ARN *suffixes*, not full ARNs — CloudWatch uses these as metric dimensions.
# Both are required to enable ALB metrics; leave unset to skip them.
# alb_arn_suffix          = "app/prod-alb/1234567890abcdef"
# target_group_arn_suffix = "targetgroup/payment-api/abcdef1234567890"

# Leave unset to disable that collector; the investigation degrades gracefully.
# splunk_host  = "https://splunk.example.com:8089"
# splunk_index = "prod"
# grafana_url  = "https://grafana.example.com"

# bedrock_model_id = "anthropic.claude-opus-5"
# bedrock_effort   = "high"

# slack_channel           = "#incidents"
# ops_email_subscriptions = ["sre-oncall@example.com"]

# lambda_memory_mb            = 1024
# lambda_timeout_seconds      = 300
# lambda_reserved_concurrency = 5
# log_retention_days          = 30
# log_level                   = "INFO"
# lookback_minutes            = 15

# Account-wide singleton per region — leave false unless this stack owns it.
# enable_bedrock_invocation_logging = false
