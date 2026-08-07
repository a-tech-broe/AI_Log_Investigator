# Third-party credentials live in one JSON secret. Terraform creates the
# container and seeds placeholders; the real values are rotated in out of band
# so they never enter state or version control.

resource "aws_secretsmanager_secret" "integrations" {
  name        = "${var.name_prefix}/integrations"
  description = "Splunk, Slack, and Grafana credentials for the AI Log Investigator."
  kms_key_id  = var.kms_key_arn == "" ? null : var.kms_key_arn

  recovery_window_in_days = var.recovery_window_days
}

resource "aws_secretsmanager_secret_version" "placeholder" {
  secret_id = aws_secretsmanager_secret.integrations.id

  secret_string = jsonencode({
    splunk_token      = "REPLACE_ME"
    slack_webhook_url = "REPLACE_ME"
    slack_bot_token   = ""
    grafana_token     = ""
  })

  # Terraform seeds the shape once; operators own the values from then on.
  lifecycle {
    ignore_changes = [secret_string]
  }
}
