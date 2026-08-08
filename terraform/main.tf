data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  name_prefix   = "${var.name_prefix}-${var.environment}"
  function_name = "${var.name_prefix}-${var.environment}"

  # Constructed rather than referenced so the IAM role can grant log access
  # without creating a role <-> function dependency cycle.
  log_group_name = "/aws/lambda/${local.function_name}"
  log_group_arn  = "arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:${local.log_group_name}"
}

# --- Supporting resources -----------------------------------------------------

module "s3" {
  source = "./modules/s3"

  name_prefix    = local.name_prefix
  account_id     = data.aws_caller_identity.current.account_id
  retention_days = var.evidence_retention_days
  kms_key_arn    = var.kms_key_arn
}

module "secrets" {
  source = "./modules/secrets-manager"

  name_prefix = local.name_prefix
  kms_key_arn = var.kms_key_arn
}

module "sns" {
  source = "./modules/sns"

  name_prefix         = local.name_prefix
  account_id          = data.aws_caller_identity.current.account_id
  email_subscriptions = var.ops_email_subscriptions
}

module "bedrock" {
  source = "./modules/bedrock"

  name_prefix               = local.name_prefix
  aws_region                = var.aws_region
  account_id                = data.aws_caller_identity.current.account_id
  model_ids                 = [var.bedrock_model_id]
  enable_invocation_logging = var.enable_bedrock_invocation_logging
  log_retention_days        = var.log_retention_days
}

# --- Identity -----------------------------------------------------------------

module "iam" {
  source = "./modules/iam"

  name_prefix         = local.name_prefix
  aws_region          = var.aws_region
  log_group_arn       = local.log_group_arn
  ecs_cluster_arns    = var.ecs_cluster_arns
  bedrock_model_arns  = module.bedrock.model_arns
  secret_arn          = module.secrets.secret_arn
  evidence_bucket_arn = module.s3.bucket_arn
  sns_topic_arn       = module.sns.topic_arn
  dlq_arn             = module.eventbridge.dlq_arn
  kms_key_arn         = var.kms_key_arn
}

# --- Compute ------------------------------------------------------------------

module "lambda" {
  source = "./modules/lambda"

  function_name        = local.function_name
  role_arn             = module.iam.role_arn
  package_path         = var.lambda_package_path
  artifact_bucket      = var.lambda_artifact_bucket
  artifact_key         = var.lambda_artifact_key
  memory_mb            = var.lambda_memory_mb
  timeout_seconds      = var.lambda_timeout_seconds
  reserved_concurrency = var.lambda_reserved_concurrency
  log_retention_days   = var.log_retention_days
  kms_key_arn          = var.kms_key_arn
  dlq_arn              = module.eventbridge.dlq_arn

  environment_variables = {
    ENVIRONMENT             = var.environment
    LOG_LEVEL               = var.log_level
    DRY_RUN                 = tostring(var.dry_run)
    ECS_CLUSTER             = var.ecs_cluster_name
    ALB_ARN_SUFFIX          = var.alb_arn_suffix
    TARGET_GROUP_ARN_SUFFIX = var.target_group_arn_suffix
    LOOKBACK_MINUTES        = tostring(var.lookback_minutes)
    SPLUNK_HOST             = var.splunk_host
    SPLUNK_INDEX            = var.splunk_index
    SPLUNK_MAX_EVENTS       = tostring(var.splunk_max_events)
    GRAFANA_URL             = var.grafana_url
    BEDROCK_MODEL_ID        = var.bedrock_model_id
    BEDROCK_EFFORT          = var.bedrock_effort
    BEDROCK_MAX_TOKENS      = tostring(var.bedrock_max_tokens)
    SLACK_CHANNEL           = var.slack_channel
    SECRET_ARN              = module.secrets.secret_arn
    EVIDENCE_BUCKET         = module.s3.bucket_name
    SNS_TOPIC_ARN           = module.sns.topic_arn
  }
}

# --- Routing ------------------------------------------------------------------

module "eventbridge" {
  source = "./modules/eventbridge"

  name_prefix          = local.name_prefix
  create_event_bus     = var.create_event_bus
  event_source_name    = var.event_source_name
  event_detail_type    = var.event_detail_type
  lambda_function_name = module.lambda.function_name
  lambda_function_arn  = module.lambda.function_arn
  lambda_alias_arn     = module.lambda.alias_arn
  lambda_alias_name    = module.lambda.alias_name
  kms_key_arn          = var.kms_key_arn
}

# --- Observability ------------------------------------------------------------

module "cloudwatch" {
  source = "./modules/cloudwatch"

  name_prefix     = local.name_prefix
  aws_region      = var.aws_region
  function_name   = module.lambda.function_name
  log_group_name  = module.lambda.log_group_name
  timeout_seconds = var.lambda_timeout_seconds
  dlq_name        = module.eventbridge.dlq_name
  sns_topic_arn   = module.sns.topic_arn
}
