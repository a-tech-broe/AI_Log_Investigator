variable "name_prefix" {
  description = "Prefix applied to the secret name."
  type        = string
}

variable "kms_key_arn" {
  description = "Customer-managed KMS key ARN. Empty uses the AWS-managed key."
  type        = string
  default     = ""
}

variable "recovery_window_days" {
  description = "Deletion recovery window. 0 deletes immediately (useful for ephemeral environments)."
  type        = number
  default     = 7
}
