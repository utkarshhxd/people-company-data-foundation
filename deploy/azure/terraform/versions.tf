# Remote state lives in the storage account deploy/azure/terraform/bootstrap
# creates. The backend block is intentionally partial -- account and
# container names are per-environment and per-subscription, so they are
# never hardcoded here; they are supplied with -backend-config at init time
# (see this directory's README).
terraform {
  required_version = ">= 1.5"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.90"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  backend "azurerm" {}
}

provider "azurerm" {
  features {}
}
