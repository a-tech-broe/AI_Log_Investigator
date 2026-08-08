variable "aws_region" {
  description = "AWS region for all resources."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment name (prod, staging, dev)."
  type        = string
  default     = "prod"
}

variable "name_prefix" {
  description = "Prefix applied to every resource name."
  type        = string
  default     = "ai-log-investigator"
}

# --- Lambda -------------------------------------------------------------------

variable "lambda_package_path" {
  description = "Path to the deployment zip built by scripts/build_lambda.sh."
  type        = string
  default     = "../build/lambda.zip"
}

variable "lambda_artifact_bucket" {
  description = "S3 bucket holding the deployment zip. Empty means upload the local file directly."
  type        = string
  default     = ""
}

variable "lambda_artifact_key" {
  description = "S3 key of the deployment zip. Required when lambda_artifact_bucket is set."
  type        = string
  default     = ""
}

variable "lambda_memory_mb" {
  description = "Lambda memory. Evidence collection is I/O bound but the JSON payloads are large."
  type        = number
  default     = 1024

  validation {
    condition     = var.lambda_memory_mb >= 512 && var.lambda_memory_mb <= 10240
    error_message = "lambda_memory_mb must be between 512 and 10240."
  }
}

variable "lambda_timeout_seconds" {
  description = "Lambda timeout. Must exceed collection time plus Bedrock inference at the configured effort."
  type        = number
  default     = 300

  validation {
    condition     = var.lambda_timeout_seconds >= 60 && var.lambda_timeout_seconds <= 900
    error_message = "lambda_timeout_seconds must be between 60 and 900."
  }
}

variable "lambda_reserved_concurrency" {
  description = <<-EOT
    Reserved concurrency, which caps Bedrock spend during an alert storm.

    -1 (the default) reserves nothing. A reservation is only possible when the
    account's concurrency limit exceeds the minimum unreserved concurrency AWS
    enforces — new accounts are often capped at 10 total, which leaves no room
    to reserve any. Check with:

      aws lambda get-account-settings --query AccountLimit.ConcurrentExecutions

    0 is valid but throttles the function to zero, disabling it entirely.
  EOT
  type        = number
  default     = -1

  validation {
    condition     = var.lambda_reserved_concurrency >= -1
    error_message = "lambda_reserved_concurrency must be -1 (no reservation) or >= 0."
  }
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for the Lambda log group."
  type        = number
  default     = 30
}

variable "log_level" {
  description = "Application log level."
  type        = string
  default     = "INFO"

  validation {
    condition     = contains(["DEBUG", "INFO", "WARNING", "ERROR"], var.log_level)
    error_message = "log_level must be one of DEBUG, INFO, WARNING, ERROR."
  }
}

# --- Investigation targets ----------------------------------------------------

variable "ecs_cluster_name" {
  description = "Default ECS cluster investigated when the alert carries no cluster label."
  type        = string
}

variable "ecs_cluster_arns" {
  description = "ECS cluster ARNs the Lambda may describe. Empty grants account-wide read within the region."
  type        = list(string)
  default     = []
}

variable "alb_arn_suffix" {
  description = "ALB ARN suffix (app/name/id) used as a CloudWatch dimension. Empty disables ALB metrics."
  type        = string
  default     = ""
}

variable "target_group_arn_suffix" {
  description = "Target group ARN suffix (targetgroup/name/id) used as a CloudWatch dimension."
  type        = string
  default     = ""
}

variable "lookback_minutes" {
  description = "How far before the alert to search for evidence."
  type        = number
  default     = 15
}

# --- Splunk / Grafana ---------------------------------------------------------

variable "splunk_host" {
  description = "Splunk management endpoint, e.g. https://splunk.example.com:8089."
  type        = string
  default     = ""
}

variable "splunk_index" {
  description = "Splunk index to search."
  type        = string
  default     = "prod"
}

variable "splunk_max_events" {
  description = "Maximum events pulled per search before client-side reduction."
  type        = number
  default     = 500
}

variable "grafana_url" {
  description = "Grafana base URL for annotation lookups. Empty disables the collector."
  type        = string
  default     = ""
}

# --- Bedrock ------------------------------------------------------------------

variable "bedrock_model_id" {
  description = "Bedrock model ID used for incident analysis."
  type        = string
  default     = "anthropic.claude-opus-5"
}

variable "bedrock_effort" {
  description = "Reasoning effort: low, medium, high, xhigh, or max."
  type        = string
  default     = "high"

  validation {
    condition     = contains(["low", "medium", "high", "xhigh", "max"], var.bedrock_effort)
    error_message = "bedrock_effort must be one of low, medium, high, xhigh, max."
  }
}

variable "bedrock_max_tokens" {
  description = "Maximum output tokens per analysis, covering thinking plus the report."
  type        = number
  default     = 16000
}

variable "enable_bedrock_invocation_logging" {
  description = "Enable account-level Bedrock model invocation logging. Singleton per region."
  type        = bool
  default     = false
}

# --- Notifications ------------------------------------------------------------

variable "slack_channel" {
  description = "Slack channel used when authenticating with a bot token."
  type        = string
  default     = "#incidents"
}

variable "ops_email_subscriptions" {
  description = "Email addresses notified when the investigator itself fails."
  type        = list(string)
  default     = []
}

# --- EventBridge --------------------------------------------------------------

variable "create_event_bus" {
  description = "Create a dedicated event bus. When false, the rule attaches to the default bus."
  type        = bool
  default     = true
}

variable "event_source_name" {
  description = "Value matched against the event `source` field."
  type        = string
  default     = "grafana.alerting"
}

variable "event_detail_type" {
  description = "Value matched against `detail-type`. Empty matches any detail-type from the source."
  type        = string
  default     = ""
}

# --- Storage ------------------------------------------------------------------

variable "evidence_retention_days" {
  description = "Days to retain archived investigations in S3."
  type        = number
  default     = 90
}

variable "kms_key_arn" {
  description = "Customer-managed KMS key for the secret and bucket. Empty uses AWS-managed keys."
  type        = string
  default     = ""
}
