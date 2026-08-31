output "master_public_ip" {
  value = azurerm_public_ip.master.ip_address
}

output "worker_public_ips" {
  value = azurerm_public_ip.worker[*].ip_address
}

output "ssh_master_command" {
  value = "ssh ${var.admin_username}@${azurerm_public_ip.master.ip_address}"
}

output "kubeconfig_command" {
  description = "Copies the master's kubeconfig locally, pointed at its public IP (already rewritten by install-server.sh.tpl)."
  value       = "scp ${var.admin_username}@${azurerm_public_ip.master.ip_address}:~/.kube/config ./kubeconfig"
}
