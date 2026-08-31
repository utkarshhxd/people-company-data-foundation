output "resource_group_name" {
  value = azurerm_resource_group.state.name
}

output "storage_account_name" {
  value = azurerm_storage_account.state.name
}

output "container_name" {
  value = azurerm_storage_container.tfstate.name
}

output "backend_config_hint" {
  description = "Pass these to `terraform init` for the main module."
  value       = <<-EOT
    terraform -chdir=deploy/azure/terraform init \
      -backend-config="resource_group_name=${azurerm_resource_group.state.name}" \
      -backend-config="storage_account_name=${azurerm_storage_account.state.name}" \
      -backend-config="container_name=${azurerm_storage_container.tfstate.name}" \
      -backend-config="key=pcdf-aks.tfstate"
  EOT
}
