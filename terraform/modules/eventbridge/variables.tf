variable "name_prefix" {
  description = "Prefix applied to resource names."
  type        = string
}

variable "create_event_bus" {
  description = "Create a dedicated event bus instead of using the default one."
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

variable "lambda_function_name" {
  description = "Name of the target Lambda function."
  type        = string
}

variable "lambda_function_arn" {
  description = "ARN of the target Lambda function, used to scope the DLQ policy."
  type        = string
}

variable "lambda_alias_arn" {
  description = "ARN of the Lambda alias the rule targets."
  type        = string
}

variable "lambda_alias_name" {
  description = "Name of the Lambda alias, used as the invoke-permission qualifier."
  type        = string
}

variable "enabled" {
  description = "Whether the rule is enabled. Set false to pause investigations without destroying anything."
  type        = bool
  default     = true
}

variable "kms_key_arn" {
  description = "KMS key for DLQ encryption. Empty uses SQS-managed encryption."
  type        = string
  default     = ""
}
