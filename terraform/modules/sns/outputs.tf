output "topic_arn" {
  description = "ARN of the operations alert topic."
  value       = aws_sns_topic.ops.arn
}
