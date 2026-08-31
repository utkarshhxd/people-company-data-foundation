# ADR 0022: Terraform for the cluster and registry, not a hand-run script

## Status

Accepted.

## Context

[ADR 0021](0021-the-whole-stack-on-a-cluster.md) drew the line at "the cloud
supplies a cluster and a registry, and nothing above that line is bought
from one vendor." That line held: `infra/k8s/base/` is still plain
Kubernetes, and the only Azure-specific file in the tree was
`deploy/azure/kustomization.yaml`, which changes two things — the image
registry and the `azurefile-csi` StorageClass for `pcdf-data`.

What sat on the wrong side of a different line was *how* the cluster and
registry themselves came to exist: `deploy/azure/create-cluster.sh` ran
`az group create`, `az acr create` and `az aks create` by hand, with
defaults baked into shell variables and no record anywhere of what had
actually been created for a given environment beyond whatever the operator
remembered to write down. It was a good script — it checked quota and
resource-provider registration before spending money, and it asked for
confirmation — but it was still imperative: run it twice with different
defaults and there is no diff to review, only two clusters that happen to
differ.

Separately, the shape asked for changed: one node (the 4-vCPU trial-quota
ceiling) is not what a cluster meant to run for real should look like.
Splitting system and workload pods onto separate node pools — one system
node, two user nodes — is ordinary AKS practice once trial-quota is not the
constraint.

## Decision

Replace `create-cluster.sh` with a Terraform module,
[`deploy/azure/terraform`](../../deploy/azure/terraform/). It provisions
exactly what the script did — one resource group, one ACR (Basic) registry,
one AKS cluster, ACR pull access wired to the cluster's kubelet identity —
plus the node pool split: `system_node_count` (default 1) on the cluster's
default pool, `user_node_count` (default 2) on a separate pool. Nothing
above that changes: no managed Postgres, no managed Kafka, no managed
Grafana. ADR 0021's line stands exactly where it was drawn.

State is remote, in an Azure Storage Account, because a cluster's
provisioning record living only on whichever laptop ran `terraform apply`
is the same problem as the shell script in a different shape. A small
bootstrap module (`deploy/azure/terraform/bootstrap`, local state) creates
that storage account once per environment — Terraform cannot create the
backend it is about to use, so this one step stays imperative by
necessity.

`user_node_count` can be set to `0` to reproduce the old single-node trial
shape (paired with `deploy/azure-trial`, unchanged) — the 4-vCPU trial
ceiling did not go away, it became a variable instead of the only option.

## Consequences

- Every environment's cluster shape is a file in version control, not a
  memory of which flags someone passed.
- `terraform plan` shows what would change before it changes — the shell
  script's confirmation prompt only ever confirmed "create," never "here
  is the diff."
- The default shape now needs 12 vCPUs (1 system + 2 user, `Standard_D4as_v5`
  each) rather than 4, which a trial subscription does not have. This is a
  real cost/quota increase versus the old default, not a wash — see
  `deploy/azure/terraform/README.md` for the arithmetic and the
  `user_node_count = 0` fallback.
- One more prerequisite to install (Terraform itself), on top of the `az`
  CLI the module still shells out to implicitly via the `azurerm` provider.
