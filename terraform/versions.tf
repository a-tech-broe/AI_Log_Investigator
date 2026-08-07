terraform {
  # 1.10 is the floor: the S3 backend's `use_lockfile` (native state locking via
  # conditional writes) does not exist before it. No DynamoDB lock table needed.
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Configured at init time so the same code works across environments:
  #   terraform init -backend-config=backend/prod.hcl
  backend "s3" {}
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "ai-log-investigator"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
