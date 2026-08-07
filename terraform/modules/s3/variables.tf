variable "name_prefix" {
  description = "Prefix applied to resource names."
  type        = string
}

variable "account_id" {
  description = "AWS account ID, appended to keep the bucket name globally unique."
  type        = string
}

variable "retention_days" {
  description = "Days to retain archived investigations."
  type        = number
  default     = 90
}

variable "kms_key_arn" {
  description = "Customer-managed KMS key ARN. Empty uses SSE-S3."
  type        = string
  default     = ""
}

variable "force_destroy" {
  description = "Allow terraform destroy to delete a non-empty bucket."
  type        = bool
  default     = false
}
