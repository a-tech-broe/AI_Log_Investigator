# Execution role for the investigator Lambda.
#
# The Lambda is read-only against production: it describes ECS and reads metrics
# and logs, but has no mutating permission on any investigated service. The only
# writes it can perform are to its own log group, its evidence bucket, and its
# ops SNS topic.

data "aws_iam_policy_document" "assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name                 = "${var.name_prefix}-lambda"
  description          = "Execution role for the AI Log Investigator Lambda."
  assume_role_policy   = data.aws_iam_policy_document.assume.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "lambda" {
  statement {
    sid       = "WriteOwnLogs"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${var.log_group_arn}:*"]
  }

  # ECS and CloudWatch read APIs do not support resource-level permissions for
  # the List/Get calls, so those are scoped by region via the condition below.
  statement {
    sid    = "DescribeEcs"
    effect = "Allow"
    actions = [
      "ecs:ListServices",
      "ecs:ListTasks",
      "ecs:DescribeClusters",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }

  statement {
    sid    = "DescribeEcsResources"
    effect = "Allow"
    actions = [
      "ecs:DescribeServices",
      "ecs:DescribeTasks",
      "ecs:DescribeTaskDefinition",
    ]
    resources = length(var.ecs_cluster_arns) > 0 ? concat(
      var.ecs_cluster_arns,
      [for arn in var.ecs_cluster_arns : replace(arn, ":cluster/", ":service/")],
      [for arn in var.ecs_cluster_arns : replace(arn, ":cluster/", ":task/")],
    ) : ["*"]
  }

  statement {
    sid    = "ReadMetrics"
    effect = "Allow"
    actions = [
      "cloudwatch:GetMetricData",
      "cloudwatch:GetMetricStatistics",
      "cloudwatch:ListMetrics",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }

  statement {
    sid       = "InvokeBedrock"
    effect    = "Allow"
    actions   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = var.bedrock_model_arns
  }

  statement {
    sid       = "ReadIntegrationSecret"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.secret_arn]
  }

  statement {
    sid       = "ArchiveEvidence"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${var.evidence_bucket_arn}/investigations/*"]
  }

  statement {
    sid       = "NotifyOperators"
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [var.sns_topic_arn]
  }

  # Required by the function's dead_letter_config. Lambda validates this grant
  # during CreateFunction, so the policy must exist before the function does —
  # see the depends_on in outputs.tf.
  statement {
    sid       = "WriteToDeadLetterQueue"
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [var.dlq_arn]
  }

  # Required by tracing_config mode = "Active". Unlike the DLQ grant this is not
  # validated at create time — without it tracing silently produces no segments.
  # X-Ray does not support resource-level permissions for these actions.
  statement {
    sid    = "WriteXRayTraces"
    effect = "Allow"
    actions = [
      "xray:PutTraceSegments",
      "xray:PutTelemetryRecords",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "lambda" {
  name   = "investigator-permissions"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda.json
}

# Decrypting the secret and writing encrypted objects needs explicit KMS grants
# when a customer-managed key is in play.
data "aws_iam_policy_document" "kms" {
  count = var.kms_key_arn == "" ? 0 : 1

  statement {
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "kms" {
  count = var.kms_key_arn == "" ? 0 : 1

  name   = "kms-access"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.kms[0].json
}
