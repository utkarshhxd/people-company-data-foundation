variable "resource_group" {
  description = "Resource group for the VMs."
  type        = string
  default     = "pcdf"
}

# southindia only has the newer B-series (v2), whose smallest size is
# 2 vCPU -- three nodes at 2 vCPU minimum each blows the 4-vCPU free-trial
# regional cap. indiasouthcentral still carries the legacy B1s/B1ms/B2s/B2ms
# family unrestricted, so 1 master (B2s, 2 vCPU) + 2 workers (B1ms, 1 vCPU
# each) fits the cap exactly.
variable "location" {
  description = "Azure region."
  type        = string
  default     = "indiasouthcentral"
}

# B-series (burstable) instead of the D4as_v5 the old AKS module used --
# Azure free-trial subscriptions cap regional vCPU quota at 4 total, and
# 1 master + 2 workers on D-series would blow past that alone. 2+1+1 = 4
# fits the quota exactly, so the architecture can actually be tested there.
variable "master_vm_size" {
  description = "VM size for the k3s server (master) node."
  type        = string
  default     = "Standard_B2s" # 2 vCPU / 4 GiB
}

variable "worker_vm_size" {
  description = "VM size for each k3s agent (worker) node."
  type        = string
  default     = "Standard_B1ms" # 1 vCPU / 2 GiB
}

# "1 master" is not a variable: a single k3s server node is the shape asked
# for, and k3s's embedded-etcd HA mode (multiple servers) is a bigger,
# separate change (see this directory's README).
variable "worker_count" {
  description = "Number of k3s agent (worker) nodes -- the \"2 worker nodes\" in the requested shape."
  type        = number
  default     = 2
}

variable "admin_username" {
  description = "Linux admin user created on every VM, and the owner of the kubeconfig copied out on the master."
  type        = string
  default     = "pcdf"
}

# No default on purpose: a default here would be a public key nobody
# generated, silently locking everyone out. Pass the contents of a real
# public key, e.g. -var="ssh_public_key=$(cat ~/.ssh/id_rsa.pub)".
variable "ssh_public_key" {
  description = "Public key (contents, not a path) for SSH access to every node."
  type        = string
}

variable "admin_cidr" {
  description = "CIDR allowed to reach SSH (22) and the k3s API (6443) from outside the VNet. Default is open -- narrow this to your own IP/32 before applying anywhere that matters."
  type        = string
  default     = "0.0.0.0/0"
}
