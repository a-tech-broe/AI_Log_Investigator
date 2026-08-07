terraform {
  required_version = ">= 1.6.0"

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
