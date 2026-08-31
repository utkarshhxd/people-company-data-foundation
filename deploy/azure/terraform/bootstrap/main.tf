# Creates the one thing the main module cannot create for itself: somewhere
# to put its remote state before it exists. Run this once per environment,
# note the outputs, and never touch it again unless the state backend itself
# needs to move.
#
# This module's own state stays local (terraform.tfstate next to this file,
# gitignored) -- there is nothing else to bootstrap it with, and it changes
# rarely enough that local state is not a problem.
#
#   terraform -chdir=deploy/azure/terraform/bootstrap init
#   terraform -chdir=deploy/azure/terraform/bootstrap apply

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
}

provider "azurerm" {
  features {}
}

variable "resource_group" {
  description = "Resource group for the Terraform state storage account."
  type        = string
  default     = "pcdf-tfstate"
}

variable "location" {
  description = "Azure region for the state storage account."
  type        = string
  default     = "southindia"
}

# Storage account names are globally unique across Azure and may only contain
# lowercase letters and digits -- the same constraint create-cluster.sh's
# REGISTRY name has today, solved the same way: a short random suffix so a
# fixed name does not collide with whoever bootstraps a second environment.
resource "random_id" "suffix" {
  byte_length = 4
}

resource "azurerm_resource_group" "state" {
  name     = var.resource_group
  location = var.location
}

resource "azurerm_storage_account" "state" {
  name                     = "pcdftfstate${random_id.suffix.hex}"
  resource_group_name      = azurerm_resource_group.state.name
  location                 = azurerm_resource_group.state.location
  account_tier             = "Standard"
  account_replication_type = "LRS"

  # State can contain secrets (e.g. the ACR admin credentials if ever
  # enabled). Versioning means an accidental terraform apply that corrupts
  # state is recoverable from blob history rather than gone.
  blob_properties {
    versioning_enabled = true
  }
}

resource "azurerm_storage_container" "tfstate" {
  name                  = "tfstate"
  storage_account_name  = azurerm_storage_account.state.name
  container_access_type = "private"
}
