output "lambda_function_name" {
  description = "Name of the investigator Lambda — pass to `aws lambda update-function-code`."
  value       = module.lambda.function_name
}

output "lambda_function_arn" {
  description = "ARN of the investigator Lambda."
  value       = module.lambda.function_arn
}

output "event_bus_name" {
  description = "Event bus Grafana should publish alerts to."
  value       = module.eventbridge.event_bus_name
}

output "event_rule_arn" {
  description = "ARN of the alert routing rule."
  value       = module.eventbridge.rule_arn
}

output "dlq_url" {
  description = "Dead-letter queue holding alerts that could not be investigated."
  value       = module.eventbridge.dlq_url
}

output "secret_arn" {
  description = "Populate this secret with splunk_token, slack_webhook_url, and grafana_token."
  value       = module.secrets.secret_arn
}

output "evidence_bucket" {
  description = "Bucket holding archived investigations."
  value       = module.s3.bucket_name
}

output "ops_topic_arn" {
  description = "SNS topic for investigator failures and alarms."
  value       = module.sns.topic_arn
}

output "log_group_name" {
  description = "CloudWatch log group for the Lambda."
  value       = module.lambda.log_group_name
}

output "dashboard_name" {
  description = "CloudWatch dashboard name."
  value       = module.cloudwatch.dashboard_name
}

output "post_apply_checklist" {
  description = "Manual steps required before the first investigation can run."
  value = [
    "1. Populate ${module.secrets.secret_arn} with splunk_token and slack_webhook_url.",
    "2. Confirm the SNS email subscriptions sent to ${join(", ", var.ops_email_subscriptions)}.",
    "3. Point Grafana's contact point at event bus '${module.eventbridge.event_bus_name}' with source '${var.event_source_name}'.",
    "4. Request Bedrock model access for ${var.bedrock_model_id} in ${var.aws_region} if not already granted.",
  ]
}
