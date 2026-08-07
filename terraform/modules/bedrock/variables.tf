variable "name_prefix" {
  description = "Prefix applied to resource names."
  type        = string
}

variable "aws_region" {
  description = "Region whose Bedrock endpoint is invoked."
  type        = string
}

variable "account_id" {
  description = "AWS account ID."
  type        = string
}

variable "model_ids" {
  description = "Bedrock model IDs the Lambda is allowed to invoke."
  type        = list(string)
}

variable "enable_invocation_logging" {
  description = "Enable Bedrock model invocation logging (account-wide singleton per region)."
  type        = bool
  default     = false
}

variable "log_retention_days" {
  description = "Retention for the invocation log group."
  type        = number
  default     = 30
}
