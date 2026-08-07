# Routes Grafana alerts to the investigator.
#
# Grafana posts to this bus via an EventBridge API destination or the AWS SDK;
# alerts that cannot be delivered after retries land in the DLQ rather than
# being dropped silently.

resource "aws_cloudwatch_event_bus" "alerts" {
  count = var.create_event_bus ? 1 : 0

  name = "${var.name_prefix}-alerts"
}

locals {
  event_bus_name = var.create_event_bus ? aws_cloudwatch_event_bus.alerts[0].name : "default"

  # An empty detail_type matches every event from the configured source.
  event_pattern = var.event_detail_type == "" ? {
    source = [var.event_source_name]
    } : {
    source        = [var.event_source_name]
    "detail-type" = [var.event_detail_type]
  }
}

resource "aws_sqs_queue" "dlq" {
  name                       = "${var.name_prefix}-dlq"
  message_retention_seconds  = 1209600 # 14 days
  visibility_timeout_seconds = 60
  sqs_managed_sse_enabled    = var.kms_key_arn == ""
  kms_master_key_id          = var.kms_key_arn == "" ? null : var.kms_key_arn
}

resource "aws_sqs_queue_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id
  policy    = data.aws_iam_policy_document.dlq.json
}

data "aws_iam_policy_document" "dlq" {
  statement {
    sid    = "AllowEventBridgeAndLambda"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com", "lambda.amazonaws.com"]
    }

    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.dlq.arn]

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_cloudwatch_event_rule.alerts.arn, var.lambda_function_arn]
    }
  }
}

resource "aws_cloudwatch_event_rule" "alerts" {
  name           = "${var.name_prefix}-grafana-alerts"
  description    = "Routes firing Grafana alerts to the AI Log Investigator."
  event_bus_name = local.event_bus_name
  event_pattern  = jsonencode(local.event_pattern)
  state          = var.enabled ? "ENABLED" : "DISABLED"
}

resource "aws_cloudwatch_event_target" "lambda" {
  rule           = aws_cloudwatch_event_rule.alerts.name
  event_bus_name = local.event_bus_name
  target_id      = "investigator"
  arn            = var.lambda_alias_arn

  retry_policy {
    maximum_event_age_in_seconds = 900
    maximum_retry_attempts       = 2
  }

  dead_letter_config {
    arn = aws_sqs_queue.dlq.arn
  }
}

resource "aws_lambda_permission" "events" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = var.lambda_function_name
  qualifier     = var.lambda_alias_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.alerts.arn
}
