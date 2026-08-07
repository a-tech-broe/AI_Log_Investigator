# Bedrock model access and (optionally) invocation logging.
#
# Model invocation logging is an account-wide singleton per region — enabling it
# from more than one stack in the same account will cause the two to fight over
# the configuration, so it is opt-in.

data "aws_partition" "current" {}

locals {
  # Inference profiles (cross-region routing) carry a different ARN shape than
  # foundation models, and callers may use either, so grant both.
  foundation_model_arns = [
    for id in var.model_ids :
    "arn:${data.aws_partition.current.partition}:bedrock:${var.aws_region}::foundation-model/${id}"
  ]

  inference_profile_arns = [
    for id in var.model_ids :
    "arn:${data.aws_partition.current.partition}:bedrock:${var.aws_region}:${var.account_id}:inference-profile/${id}"
  ]
}

resource "aws_cloudwatch_log_group" "invocations" {
  count = var.enable_invocation_logging ? 1 : 0

  name              = "/aws/bedrock/${var.name_prefix}"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "logging" {
  count = var.enable_invocation_logging ? 1 : 0

  name               = "${var.name_prefix}-bedrock-logging"
  assume_role_policy = data.aws_iam_policy_document.logging_assume[0].json
}

data "aws_iam_policy_document" "logging_assume" {
  count = var.enable_invocation_logging ? 1 : 0

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["bedrock.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
  }
}

resource "aws_iam_role_policy" "logging" {
  count = var.enable_invocation_logging ? 1 : 0

  name   = "write-invocation-logs"
  role   = aws_iam_role.logging[0].id
  policy = data.aws_iam_policy_document.logging[0].json
}

data "aws_iam_policy_document" "logging" {
  count = var.enable_invocation_logging ? 1 : 0

  statement {
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.invocations[0].arn}:*"]
  }
}

resource "aws_bedrock_model_invocation_logging_configuration" "this" {
  count = var.enable_invocation_logging ? 1 : 0

  logging_config {
    embedding_data_delivery_enabled = false
    image_data_delivery_enabled     = false
    text_data_delivery_enabled      = true

    cloudwatch_config {
      log_group_name = aws_cloudwatch_log_group.invocations[0].name
      role_arn       = aws_iam_role.logging[0].arn
    }
  }

  depends_on = [aws_iam_role_policy.logging]
}
