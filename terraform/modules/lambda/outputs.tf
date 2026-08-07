output "function_name" {
  description = "Name of the Lambda function."
  value       = aws_lambda_function.investigator.function_name
}

output "function_arn" {
  description = "ARN of the Lambda function."
  value       = aws_lambda_function.investigator.arn
}

output "alias_arn" {
  description = "ARN of the live alias — the EventBridge target."
  value       = aws_lambda_alias.live.arn
}

output "alias_name" {
  description = "Name of the live alias."
  value       = aws_lambda_alias.live.name
}

output "log_group_name" {
  description = "Name of the Lambda log group."
  value       = aws_cloudwatch_log_group.lambda.name
}
