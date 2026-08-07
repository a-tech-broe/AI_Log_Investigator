variable "name_prefix" {
  description = "Prefix applied to alarm and dashboard names."
  type        = string
}

variable "aws_region" {
  description = "Region used in dashboard widget definitions."
  type        = string
}

variable "function_name" {
  description = "Name of the Lambda function to monitor."
  type        = string
}

variable "log_group_name" {
  description = "Lambda log group name, used for metric filters and log widgets."
  type        = string
}

variable "timeout_seconds" {
  description = "Lambda timeout, used to derive the duration alarm threshold."
  type        = number
}

variable "dlq_name" {
  description = "Name of the dead-letter queue to monitor."
  type        = string
}

variable "sns_topic_arn" {
  description = "SNS topic notified when an alarm fires."
  type        = string
}

variable "metric_namespace" {
  description = "Custom metric namespace for log-derived metrics."
  type        = string
  default     = "AILogInvestigator"
}

variable "create_dashboard" {
  description = "Create the CloudWatch dashboard."
  type        = bool
  default     = true
}
