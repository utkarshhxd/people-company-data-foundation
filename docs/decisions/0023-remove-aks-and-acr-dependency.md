# ADR 0023: Self-managed k3s and ghcr.io, not AKS and ACR

## Status

Accepted. Supersedes the AKS/ACR-specific parts of
[ADR 0022](0022-terraform-for-cluster-provisioning.md); that ADR's reasoning
for using Terraform at all (a versioned provisioning record instead of a
hand-run script) still stands.

## Context

[ADR 0021](0021-the-whole-stack-on-a-cluster.md) drew the line at "the cloud
supplies a cluster and a registry, and nothing above that line is bought
from one vendor." AKS and ACR sat exactly on that line — not above it, since
neither is a managed database or broker, but not nothing either: both are
Azure services with their own APIs, and a cluster or registry provisioned
through them is provisioned through Azure-specific code no matter how
generic what runs inside it is.

Asked directly, the answer to "are we locked into Azure at all" was "only
for the cluster and the registry, and here is exactly how much." That is a
smaller answer than "not at all." Removing AKS and ACR closes that gap: the
only Azure-specific resource left after this change is the VM itself — the
one thing every cloud, and a rack in a closet, sells the same way.

## Decision

Replace AKS with self-managed [k3s](https://k3s.io) on three plain VMs — one
server node ("1 master"), two agent nodes ("2 worker nodes"), matching the
shape asked for. Replace ACR with ghcr.io. Concretely:

- `deploy/azure/terraform` (previously provisioning an
  `azurerm_kubernetes_cluster` and an `azurerm_container_registry`) now
  provisions three `azurerm_linux_virtual_machine` resources and a VNet.
  k3s is installed by [`infra/k3s/install-server.sh.tpl`](../../infra/k3s/install-server.sh.tpl)
  and [`install-agent.sh.tpl`](../../infra/k3s/install-agent.sh.tpl) —
  neither names a cloud; they take a join token and an IP address as their
  only inputs, rendered by whichever cloud's Terraform calls them. Living
  under `infra/` rather than `deploy/azure/` is deliberate: a second cloud's
  Terraform module reuses them unchanged.
- `azurefile-csi` (AKS's Azure Files StorageClass, backing the
  `pcdf-data` ReadWriteMany volume) is replaced by a self-hosted NFS server
  pod, [`infra/k8s/base/09-nfs-server.yaml`](../../infra/k8s/base/09-nfs-server.yaml).
  `pcdf-data` itself moved from `infra/k8s/base/12-data-pvc.yaml` pinning no
  StorageClass (an overlay's job) to a statically-bound NFS
  PersistentVolume in the base — there is no longer a cloud-specific class
  name for an overlay to set, so the overlay lost that patch entirely.
- ACR's `--attach-acr` cluster-identity attachment (no `imagePullSecret`
  needed anywhere) is replaced by an explicit one:
  [`infra/k8s/base/04-registry-secret.example.yaml`](../../infra/k8s/base/04-registry-secret.example.yaml),
  referenced via `imagePullSecrets` on every Deployment/Job that pulls a
  `pcdf-*` image. This is the one place the new shape is *more* manual than
  before — ghcr.io has no per-cluster identity to attach to.
- `deploy/azure/kustomization.yaml` now changes exactly one thing about the
  base (the registry hostname) instead of two (registry and StorageClass).

## Consequences

- The only Azure-specific code in the repository is
  `deploy/azure/terraform`'s VM and network resources. Everything from the
  Kubernetes API down is identical on any cloud that can run three Linux
  VMs and route between them.
- Control-plane HA, etcd backups, k3s version upgrades and OS patching —
  all handled by AKS before — are now this team's responsibility. See
  `deploy/azure/terraform/README.md`'s "What you now own that AKS used to"
  for specifics. This is the real cost of this decision, not a rounding
  error: it trades an operational guarantee for portability.
- One more manual step per environment: creating the ghcr.io pull secret,
  where ACR needed none.
- The default provisioned shape (1 server + 2 agents, all `Standard_D4as_v5`)
  needs 12 vCPUs, same as ADR 0022's AKS shape — the quota/cost note there
  still applies, and `worker_count = 0` still gets back to a single-VM shape
  that fits a free-trial subscription's 4-vCPU cap.
