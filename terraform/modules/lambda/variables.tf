variable "function_name" {
  description = "Name of the Lambda function."
  type        = string
}

variable "role_arn" {
  description = "Execution role ARN."
  type        = string
}

variable "runtime" {
  description = "Lambda runtime."
  type        = string
  default     = "python3.12"
}

variable "package_path" {
  description = "Local path to the deployment zip. Used when artifact_bucket is empty."
  type        = string
  default     = ""
}

variable "artifact_bucket" {
  description = "S3 bucket holding the deployment zip. Empty uploads package_path directly."
  type        = string
  default     = ""
}

variable "artifact_key" {
  description = "S3 key of the deployment zip."
  type        = string
  default     = ""
}

variable "memory_mb" {
  description = "Memory allocated to the function."
  type        = number
  default     = 1024
}

variable "timeout_seconds" {
  description = "Function timeout."
  type        = number
  default     = 300
}

variable "reserved_concurrency" {
  description = "Reserved concurrency. -1 disables the reservation."
  type        = number
  default     = 5
}

variable "environment_variables" {
  description = "Environment variables passed to the function."
  type        = map(string)
  default     = {}
}

variable "dlq_arn" {
  description = "SQS queue ARN for asynchronous invocation failures."
  type        = string
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention."
  type        = number
  default     = 30
}

variable "kms_key_arn" {
  description = "KMS key for log encryption. Empty uses the AWS-managed key."
  type        = string
  default     = ""
}
