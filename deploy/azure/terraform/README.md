# Provisioning a self-managed k3s cluster with Terraform

Replaces the earlier AKS-based module (see
`docs/decisions/0023-remove-aks-and-acr-dependency.md`). This module
provisions three plain Azure VMs -- one k3s server ("1 master"), two k3s
agents ("2 worker nodes") -- and nothing that is an Azure *Kubernetes*
service. AKS and ACR are gone: Kubernetes itself now runs as k3s, installed
by [`infra/k3s/install-server.sh.tpl`](../../../infra/k3s/install-server.sh.tpl)
and [`install-agent.sh.tpl`](../../../infra/k3s/install-agent.sh.tpl), which
name no cloud -- the only Azure-specific code left is in this directory,
provisioning VMs, a VNet and a couple of public IPs, the same category of
resource every cloud sells.

**THIS SPENDS MONEY.** One `Standard_B2s` master (2 vCPU / 4 GiB) and two
`Standard_B1ms` workers (1 vCPU / 2 GiB each) -- 4 vCPU total, sized to fit
inside an Azure free trial's regional vCPU quota -- plus their disks and
three Standard public IPs, running from the moment `apply` finishes. Check
current prices for your `location` before applying. Stop paying without
losing anything:

```
az vm deallocate --ids $(az vm list -g <resource_group> --query "[].id" -o tsv)
```

## What replaced what

| Before (AKS) | Now |
|---|---|
| AKS cluster | 3 VMs running k3s (1 server + 2 agents) |
| ACR registry | ghcr.io -- see `infra/k8s/base/04-registry-secret.example.yaml` |
| `azurefile-csi` StorageClass for `pcdf-data` | Self-hosted NFS server pod, `infra/k8s/base/09-nfs-server.yaml` |
| `--attach-acr` role assignment | A Kubernetes `imagePullSecret`, same as any other cluster pulling from any other registry |

## Prerequisites

- Terraform >= 1.5, the Azure CLI, logged in, a subscription selected.
- An SSH key pair. This module takes the *public* key as a required
  variable (`ssh_public_key`) -- generate one if you don't have one:
  `ssh-keygen -t ed25519 -f ./pcdf-cluster-key`.

## One-time: bootstrap remote state

Unchanged from before -- see [`bootstrap/`](bootstrap/), run once per
environment:

```
terraform -chdir=deploy/azure/terraform/bootstrap init
terraform -chdir=deploy/azure/terraform/bootstrap apply
```

## Provision the cluster

```
terraform -chdir=deploy/azure/terraform init \
  -backend-config="resource_group_name=<from bootstrap output>" \
  -backend-config="storage_account_name=<from bootstrap output>" \
  -backend-config="container_name=tfstate" \
  -backend-config="key=pcdf-cluster.tfstate"

terraform -chdir=deploy/azure/terraform plan \
  -var="ssh_public_key=$(cat ./pcdf-cluster-key.pub)"

terraform -chdir=deploy/azure/terraform apply \
  -var="ssh_public_key=$(cat ./pcdf-cluster-key.pub)"
```

`admin_cidr` defaults to open (`0.0.0.0/0`) for SSH and the k3s API port.
Narrow it to your own IP with `-var="admin_cidr=<your-ip>/32"` before
applying anywhere that matters.

Review the plan before applying -- this is real, billable infrastructure.

## After apply

```
terraform -chdir=deploy/azure/terraform output
```

In order:

1. Get the kubeconfig: run the `kubeconfig_command` output, then
   `export KUBECONFIG=./kubeconfig`. `kubectl get nodes` should show three
   `Ready` nodes once cloud-init finishes (a minute or two after `apply`).
2. Create the registry pull secret from
   `infra/k8s/base/04-registry-secret.example.yaml` (copy it out, fill in a
   [GitHub PAT with `read:packages`](https://docs.github.com/packages/working-with-a-github-packages-registry/working-with-the-container-registry),
   apply). Same pattern as `02-secret.example.yaml`.
3. Build and push images to ghcr.io:
   `REGISTRY=ghcr.io/<your-org> sh infra/scripts/build-and-push.sh`
4. Point `deploy/azure/kustomization.yaml`'s `images:` block at that same
   `ghcr.io/<your-org>` prefix.
5. Create `pcdf-secrets` from `infra/k8s/base/02-secret.example.yaml`, then
   `kubectl apply -k deploy/azure`. Full sequence: `infra/k8s/README.md`.

An ingress controller is NOT installed (ingress-nginx is the portable
choice -- ports 80/443 are already open in this module's NSG for it). The
console is reachable without one:
`kubectl port-forward -n pcdf svc/review-console 8000:8000`. Read
`infra/k8s/base/31-review-console-ingress.yaml` before making it public --
it serves personal data with full provenance.

## What you now own that AKS used to

- **Control-plane HA**: one k3s server, one point of failure for the API
  server. k3s supports multi-server embedded-etcd HA; adding it means more
  server nodes and is a separate change from this module's default shape.
- **etcd backups**: k3s snapshots its embedded datastore to
  `/var/lib/rancher/k3s/server/db/snapshots` on the master by default, but
  nothing ships those off the VM. Back that directory up the same way
  `infra/k8s/base/14-postgres-backup.yaml` backs up Postgres.
- **Upgrades**: `curl -sfL https://get.k3s.io | sh -` again upgrades k3s
  in place; there is no `az aks upgrade` doing this on a schedule.
- **OS patching**: Ubuntu's unattended-upgrades is not configured by this
  module. Azure patched AKS node images; these are plain VMs.

## Tearing down

```
terraform -chdir=deploy/azure/terraform destroy -var="ssh_public_key=$(cat ./pcdf-cluster-key.pub)"
terraform -chdir=deploy/azure/terraform/bootstrap destroy   # only if the state storage itself is no longer needed
```
