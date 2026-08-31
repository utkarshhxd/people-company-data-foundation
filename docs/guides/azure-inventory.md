# What's actually in Azure right now

A live inventory, not a design doc — for the reasoning behind why any of
this exists, read [ADR 0021](../decisions/0021-the-whole-stack-on-a-cluster.md),
[0022](../decisions/0022-terraform-for-cluster-provisioning.md) and
[0023](../decisions/0023-remove-aks-and-acr-dependency.md), and
[`deploy/azure/terraform/README.md`](../../deploy/azure/terraform/README.md).
This document only answers "what's deployed at this moment, and which of the
three ADRs put it there" — it will go stale the next time someone runs
`terraform apply` or `destroy`. Regenerate it with:

```
az resource list -o table
```

Snapshot taken 2026-08-27, subscription `Azure subscription 1`
(`263ba326-248d-46c7-a28c-a46b1dee0960`), tenant `m.rahulraj24@gmail.com`.

## Resource groups

| Resource group | Region | Created by | Purpose |
|---|---|---|---|
| `pcdf` | southindia | `deploy/azure/terraform` (main module) | The k3s cluster's VMs and networking |
| `pcdf-tfstate` | southindia | `deploy/azure/terraform/bootstrap` | Terraform's own remote state backend |
| `NetworkWatcherRG` | centralindia + southindia | Azure itself, automatically | Not ours — see below |

## `pcdf` — the cluster

Everything here comes from the Terraform module ADR 0023 introduced,
replacing what used to be an AKS cluster and an ACR registry.

| Resource | Type | Notes |
|---|---|---|
| `pcdf-master` | VM, `Standard_D4as_v5` (4 vCPU / 16 GiB), Ubuntu 22.04 LTS | The k3s **server** node. Runs `infra/k3s/install-server.sh.tpl` via cloud-init. |
| `pcdf-master_OsDisk_...` | Managed disk, StandardSSD_LRS | OS disk for the master, in a resource group Azure lists as `PCDF` (case-folded by the disk API — same group). |
| `pcdf-master-ip` | Public IP, Standard SKU, static | `20.41.228.215` — SSH (22) and the k3s API (6443) reach the master here. |
| `pcdf-master-nic` | NIC | Attaches the public IP and the NSG to the master. |
| `pcdf-nodes-nsg` | Network security group | Opens 22, 6443, 80, 443 — the last two for an ingress controller not yet installed (see the Terraform README). |
| `pcdf-vnet` | Virtual network | Holds the node subnet. |

**Only the master exists right now.** `variables.tf` defaults
`worker_count` to `2` (a server plus two agents, matching the "1 master, 2
worker nodes" shape ADR 0023 describes), but this subscription has a single
VM. Whoever last applied this module either passed `-var="worker_count=0"`
or hasn't applied the workers yet — check
`terraform -chdir=deploy/azure/terraform state list` against a fresh `plan`
before assuming either. A single-node k3s cluster is still a complete,
working cluster (k3s server nodes schedule pods by default) — it's the
`deploy/azure-trial` overlay's shape, not `deploy/azure`'s.

## `pcdf-tfstate` — Terraform's own state

| Resource | Type | Notes |
|---|---|---|
| `pcdftfstated1060b0f` | Storage account, StorageV2, Standard_LRS | Holds the `tfstate` blob container. Created once by `deploy/azure/terraform/bootstrap`, blob versioning on so a bad `apply` is recoverable from history. |

This is infrastructure-about-infrastructure: it exists so that `pcdf`'s
Terraform state isn't sitting on one operator's laptop. Nothing in `pcdf`
depends on it at runtime — losing it only costs the provisioning record, not
the running cluster.

## `NetworkWatcherRG` — not something anyone created

| Resource | Type | Notes |
|---|---|---|
| `NetworkWatcher_centralindia` | Network Watcher | Auto-created |
| `NetworkWatcher_southindia` | Network Watcher | Auto-created |

Azure creates one of these automatically, per region, the first time a
virtual network shows up in that region on the subscription — no one ran
`az network watcher` by hand. `centralindia` having one despite nothing else
in this inventory being in that region suggests either an earlier,
since-deleted resource, or the subscription-wide default enabling it before
any VNet existed. Harmless and free at this scale; safe to leave alone.

## What's deliberately *not* here

Per ADR 0021, nothing above "a VM and a network" is bought from Azure:
no managed Postgres, no managed Kafka (Event Hubs was rejected — see the
ADR), no managed Grafana, no AKS, no ACR (replaced by ghcr.io per ADR 0023).
Postgres, Kafka, Prometheus, Alertmanager, Grafana and the application
services all run as pods inside the one cluster this inventory lists, not as
separate Azure resources — so `az resource list` will never show them
growing this file.
