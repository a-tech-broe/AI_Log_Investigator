output "event_bus_name" {
  description = "Name of the event bus Grafana should publish to."
  value       = local.event_bus_name
}

output "event_bus_arn" {
  description = "ARN of the event bus, when a dedicated one was created."
  value       = var.create_event_bus ? aws_cloudwatch_event_bus.alerts[0].arn : ""
}

output "rule_arn" {
  description = "ARN of the alert routing rule."
  value       = aws_cloudwatch_event_rule.alerts.arn
}

output "dlq_arn" {
  description = "ARN of the dead-letter queue."
  value       = aws_sqs_queue.dlq.arn
}

output "dlq_name" {
  description = "Name of the dead-letter queue."
  value       = aws_sqs_queue.dlq.name
}

output "dlq_url" {
  description = "URL of the dead-letter queue."
  value       = aws_sqs_queue.dlq.id
}
