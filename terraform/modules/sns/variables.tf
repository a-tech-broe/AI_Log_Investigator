variable "name_prefix" {
  description = "Prefix applied to resource names."
  type        = string
}

variable "account_id" {
  description = "AWS account ID, used to scope the topic policy."
  type        = string
}

variable "email_subscriptions" {
  description = "Email addresses to subscribe. Each requires manual confirmation."
  type        = list(string)
  default     = []
}

variable "kms_key_arn" {
  description = "KMS key for topic encryption. Empty uses the AWS-managed SNS key."
  type        = string
  default     = ""
}
