# Observability for the investigator itself: is it running, failing, or silently
# producing degraded reports?

resource "aws_cloudwatch_metric_alarm" "errors" {
  alarm_name          = "${var.name_prefix}-lambda-errors"
  alarm_description   = "The investigator Lambda is throwing errors."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = { FunctionName = var.function_name }

  alarm_actions = [var.sns_topic_arn]
  ok_actions    = [var.sns_topic_arn]
}

resource "aws_cloudwatch_metric_alarm" "throttles" {
  alarm_name          = "${var.name_prefix}-lambda-throttles"
  alarm_description   = "Investigations are being throttled — alerts may go uninvestigated."
  namespace           = "AWS/Lambda"
  metric_name         = "Throttles"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = { FunctionName = var.function_name }

  alarm_actions = [var.sns_topic_arn]
}

# Fires before the hard timeout so a slow path is visible while it is still
# succeeding, rather than only once invocations start dying.
resource "aws_cloudwatch_metric_alarm" "duration" {
  alarm_name          = "${var.name_prefix}-lambda-duration"
  alarm_description   = "Investigations are approaching the Lambda timeout."
  namespace           = "AWS/Lambda"
  metric_name         = "Duration"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  threshold           = var.timeout_seconds * 1000 * 0.8
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = { FunctionName = var.function_name }

  alarm_actions = [var.sns_topic_arn]
}

resource "aws_cloudwatch_metric_alarm" "dlq_depth" {
  alarm_name          = "${var.name_prefix}-dlq-not-empty"
  alarm_description   = "Alerts have landed in the DLQ — investigations were dropped."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = { QueueName = var.dlq_name }

  alarm_actions = [var.sns_topic_arn]
}

# Degraded reports are a correctness problem, not an availability one: the
# Lambda returns 200 while Bedrock analysis silently failed, so surface it from
# the logs rather than from Lambda's own error metric.
resource "aws_cloudwatch_log_metric_filter" "degraded" {
  name           = "${var.name_prefix}-degraded-reports"
  log_group_name = var.log_group_name
  pattern        = "{ $.message = \"bedrock analysis failed*\" }"

  metric_transformation {
    name          = "DegradedReports"
    namespace     = var.metric_namespace
    value         = "1"
    default_value = 0
    unit          = "Count"
  }
}

resource "aws_cloudwatch_metric_alarm" "degraded" {
  alarm_name          = "${var.name_prefix}-degraded-reports"
  alarm_description   = "Reports are being delivered without AI analysis."
  namespace           = var.metric_namespace
  metric_name         = aws_cloudwatch_log_metric_filter.degraded.metric_transformation[0].name
  statistic           = "Sum"
  period              = 900
  evaluation_periods  = 1
  threshold           = 2
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  alarm_actions = [var.sns_topic_arn]
}

resource "aws_cloudwatch_dashboard" "investigator" {
  count = var.create_dashboard ? 1 : 0

  dashboard_name = var.name_prefix

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "Investigations"
          region = var.aws_region
          view   = "timeSeries"
          metrics = [
            ["AWS/Lambda", "Invocations", "FunctionName", var.function_name, { stat = "Sum" }],
            [".", "Errors", ".", ".", { stat = "Sum" }],
            [".", "Throttles", ".", ".", { stat = "Sum" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "Investigation duration"
          region = var.aws_region
          view   = "timeSeries"
          metrics = [
            ["AWS/Lambda", "Duration", "FunctionName", var.function_name, { stat = "Average" }],
            ["...", { stat = "Maximum" }],
          ]
          annotations = {
            horizontal = [{ label = "Timeout", value = var.timeout_seconds * 1000 }]
          }
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          title  = "Dropped alerts (DLQ)"
          region = var.aws_region
          view   = "timeSeries"
          metrics = [
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", var.dlq_name, { stat = "Maximum" }],
          ]
        }
      },
      {
        type   = "log"
        x      = 12
        y      = 6
        width  = 12
        height = 6
        properties = {
          title  = "Recent investigations"
          region = var.aws_region
          query  = "SOURCE '${var.log_group_name}' | fields @timestamp, alert.service, confidence, message | filter message = 'investigation complete' | sort @timestamp desc | limit 20"
        }
      },
    ]
  })
}
