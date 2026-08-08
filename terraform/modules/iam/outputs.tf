output "role_arn" {
  description = "ARN of the Lambda execution role."
  value       = aws_iam_role.lambda.arn

  # Lambda's CreateFunction rejects a dead_letter_config whose execution role
  # cannot yet call sqs:SendMessage. The function only references the role, so
  # without this the policy can race and the create fails intermittently.
  #
  # A module-level depends_on would cycle here (iam -> eventbridge -> lambda),
  # so the ordering is pinned on the output instead.
  depends_on = [
    aws_iam_role_policy.lambda,
    aws_iam_role_policy.kms,
  ]
}

output "role_name" {
  description = "Name of the Lambda execution role."
  value       = aws_iam_role.lambda.name
}
