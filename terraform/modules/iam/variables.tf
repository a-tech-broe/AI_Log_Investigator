variable "name_prefix" {
  description = "Prefix applied to resource names."
  type        = string
}

variable "aws_region" {
  description = "Region the Lambda is permitted to read from."
  type        = string
}

variable "log_group_arn" {
  description = "ARN of the Lambda's own log group."
  type        = string
}

variable "ecs_cluster_arns" {
  description = "ECS cluster ARNs to scope describe permissions to. Empty grants account-wide read."
  type        = list(string)
  default     = []
}

variable "bedrock_model_arns" {
  description = "Bedrock model ARNs the Lambda may invoke."
  type        = list(string)
}

variable "secret_arn" {
  description = "ARN of the integrations secret."
  type        = string
}

variable "evidence_bucket_arn" {
  description = "ARN of the evidence bucket."
  type        = string
}

variable "sns_topic_arn" {
  description = "ARN of the ops alert topic."
  type        = string
}

variable "dlq_arn" {
  description = "ARN of the dead-letter queue the function writes async failures to."
  type        = string
}

variable "kms_key_arn" {
  description = "Customer-managed KMS key ARN, when one is in use."
  type        = string
  default     = ""
}
