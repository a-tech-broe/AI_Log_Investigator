# The investigator function.
#
# The log group is created here (rather than letting Lambda create it lazily) so
# retention is enforced from the first invocation. Its ARN is passed into the
# IAM module as a constructed string to avoid a role <-> function dependency cycle.

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${var.function_name}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn == "" ? null : var.kms_key_arn
}

locals {
  from_s3 = var.artifact_bucket != ""
}

resource "aws_lambda_function" "investigator" {
  function_name = var.function_name
  description   = "Investigates Grafana alerts across ECS, CloudWatch, and Splunk, then reports via Slack."
  role          = var.role_arn
  handler       = "app.handler"
  runtime       = var.runtime
  architectures = ["x86_64"]

  # Either upload the local zip or reference one already in S3 — never both.
  # try() guards the hash: when deploying from S3 there is no local zip to read.
  filename         = local.from_s3 ? null : var.package_path
  source_code_hash = local.from_s3 ? null : try(filebase64sha256(var.package_path), null)
  s3_bucket        = local.from_s3 ? var.artifact_bucket : null
  s3_key           = local.from_s3 ? var.artifact_key : null

  memory_size                    = var.memory_mb
  timeout                        = var.timeout_seconds
  reserved_concurrent_executions = var.reserved_concurrency

  environment {
    variables = var.environment_variables
  }

  dead_letter_config {
    target_arn = var.dlq_arn
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [aws_cloudwatch_log_group.lambda]
}

# EventBridge invokes the alias, so a failed deploy can be rolled back by
# repointing the alias instead of redeploying code.
resource "aws_lambda_alias" "live" {
  name             = "live"
  description      = "Alias targeted by the EventBridge rule."
  function_name    = aws_lambda_function.investigator.function_name
  function_version = "$LATEST"
}
