output "secret_arn" {
  description = "ARN of the integrations secret."
  value       = aws_secretsmanager_secret.integrations.arn
}

output "secret_name" {
  description = "Name of the integrations secret."
  value       = aws_secretsmanager_secret.integrations.name
}
