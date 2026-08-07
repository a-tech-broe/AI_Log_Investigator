output "alarm_names" {
  description = "Names of every alarm created by this module."
  value = [
    aws_cloudwatch_metric_alarm.errors.alarm_name,
    aws_cloudwatch_metric_alarm.throttles.alarm_name,
    aws_cloudwatch_metric_alarm.duration.alarm_name,
    aws_cloudwatch_metric_alarm.dlq_depth.alarm_name,
    aws_cloudwatch_metric_alarm.degraded.alarm_name,
  ]
}

output "dashboard_name" {
  description = "Name of the CloudWatch dashboard, when created."
  value       = var.create_dashboard ? aws_cloudwatch_dashboard.investigator[0].dashboard_name : ""
}
