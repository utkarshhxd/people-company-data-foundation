# Shared join secret for the cluster. Generated, not a variable, so nobody
# has to invent one -- it never leaves this module except into the two
# templates below, over an Azure VM's provisioning channel (not the network).
resource "random_password" "k3s_token" {
  length  = 32
  special = false
}

resource "azurerm_public_ip" "master" {
  name                = "${var.resource_group}-master-ip"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  allocation_method   = "Static"
  sku                 = "Standard"
}

resource "azurerm_network_interface" "master" {
  name                = "${var.resource_group}-master-nic"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location

  ip_configuration {
    name                          = "internal"
    subnet_id                     = azurerm_subnet.nodes.id
    private_ip_address_allocation = "Dynamic"
    public_ip_address_id          = azurerm_public_ip.master.id
  }
}

resource "azurerm_linux_virtual_machine" "master" {
  name                = "${var.resource_group}-master"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  size                = var.master_vm_size
  admin_username      = var.admin_username
  network_interface_ids = [azurerm_network_interface.master.id]

  admin_ssh_key {
    username   = var.admin_username
    public_key = var.ssh_public_key
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "StandardSSD_LRS"
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "0001-com-ubuntu-server-jammy"
    sku       = "22_04-lts-gen2"
    version   = "latest"
  }

  # No azurerm calls inside this script -- see infra/k3s/install-server.sh.tpl.
  # This module's only job is filling in the two values that differ by cloud.
  custom_data = base64encode(templatefile("${path.module}/../../../infra/k3s/install-server.sh.tpl", {
    k3s_token      = random_password.k3s_token.result
    admin_username = var.admin_username
    public_ip      = azurerm_public_ip.master.ip_address
  }))
}

resource "azurerm_public_ip" "worker" {
  count               = var.worker_count
  name                = "${var.resource_group}-worker-${count.index}-ip"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  allocation_method   = "Static"
  sku                 = "Standard"
}

resource "azurerm_network_interface" "worker" {
  count               = var.worker_count
  name                = "${var.resource_group}-worker-${count.index}-nic"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location

  ip_configuration {
    name                          = "internal"
    subnet_id                     = azurerm_subnet.nodes.id
    private_ip_address_allocation = "Dynamic"
    public_ip_address_id          = azurerm_public_ip.worker[count.index].id
  }
}

resource "azurerm_linux_virtual_machine" "worker" {
  count               = var.worker_count
  name                = "${var.resource_group}-worker-${count.index}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  size                = var.worker_vm_size
  admin_username      = var.admin_username
  network_interface_ids = [azurerm_network_interface.worker[count.index].id]

  admin_ssh_key {
    username   = var.admin_username
    public_key = var.ssh_public_key
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "StandardSSD_LRS"
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "0001-com-ubuntu-server-jammy"
    sku       = "22_04-lts-gen2"
    version   = "latest"
  }

  # Joins over the master's private IP -- node-to-node traffic stays inside
  # the VNet rather than round-tripping through the public internet.
  custom_data = base64encode(templatefile("${path.module}/../../../infra/k3s/install-agent.sh.tpl", {
    k3s_token = random_password.k3s_token.result
    server_ip = azurerm_network_interface.master.private_ip_address
  }))

  depends_on = [azurerm_linux_virtual_machine.master]
}
