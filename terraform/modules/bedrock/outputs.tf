output "model_arns" {
  description = "Foundation-model and inference-profile ARNs to grant bedrock:InvokeModel on."
  value       = concat(local.foundation_model_arns, local.inference_profile_arns)
}

output "invocation_log_group" {
  description = "Name of the Bedrock invocation log group, when logging is enabled."
  value       = var.enable_invocation_logging ? aws_cloudwatch_log_group.invocations[0].name : ""
}
