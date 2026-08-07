# Operator notifications for failures of the investigator itself — distinct
# from the Slack channel that receives incident reports.

resource "aws_sns_topic" "ops" {
  name              = "${var.name_prefix}-ops-alerts"
  display_name      = "AI Log Investigator alerts"
  kms_master_key_id = var.kms_key_arn == "" ? "alias/aws/sns" : var.kms_key_arn
}

resource "aws_sns_topic_policy" "ops" {
  arn    = aws_sns_topic.ops.arn
  policy = data.aws_iam_policy_document.ops.json
}

data "aws_iam_policy_document" "ops" {
  statement {
    sid    = "AllowCloudWatchAlarms"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }

    actions   = ["SNS:Publish"]
    resources = [aws_sns_topic.ops.arn]

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceAccount"
      values   = [var.account_id]
    }
  }
}

resource "aws_sns_topic_subscription" "email" {
  for_each = toset(var.email_subscriptions)

  topic_arn = aws_sns_topic.ops.arn
  protocol  = "email"
  endpoint  = each.value
}
