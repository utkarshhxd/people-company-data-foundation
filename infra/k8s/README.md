# Running this on more than one machine

Docker Compose assumes one host: every service reaches Postgres on a bridge
network only that host has, bind-mounts `./data` and `./tools` from a checkout
only that host has, and restarts a crashed container on the same node because
there is no other node. `infra/k8s/base/` is the same stack translated to run
across a cluster instead — orchestrated restarts, stable volumes and names
across reschedules, and no single machine whose disk is the whole database's
disk.

**Everything runs as pods.** Postgres, Kafka, Prometheus, Alertmanager and
Grafana are containers here exactly as they are containers under Compose. No
managed database, no managed broker, no cloud-specific resource anywhere in
`infra/`. The cloud supplies a Kubernetes cluster and a container registry —
which every cloud supplies — and nothing above that line is bought from one
vendor. The one thing that genuinely differs between clusters is which
StorageClass backs a shared-filesystem volume, and that lives in an overlay:
[`deploy/azure/`](../../deploy/azure/) is one, and `deploy/` is the only place
in this repository that knows Azure exists.

## What's here

| File | What it runs |
| --- | --- |
| `00-namespace.yaml` | `pcdf` |
| `01-configmap.yaml` | Everything a service needs that is not a credential |
| `02-secret.example.yaml` | Template for the three secrets — **not applied by kustomize** |
| `10-postgres.yaml` | Postgres StatefulSet + headless Service |
| `11-kafka.yaml` | Single-node KRaft broker StatefulSet + Service |
| `12-data-pvc.yaml` | The shared `/data` tree — **ReadWriteMany**, see below |
| `13-migrate-job.yaml` | Migrations, once, as a Job |
| `14-postgres-backup.yaml` | Nightly verified `pg_dump` CronJob + its volume |
| `20-watcher.yaml` | The watcher (the record-at-a-time path) |
| `21-` … `25-` | The five stage consumers: mapping, normalization, validation, resolution, golden |
| `30-review-console.yaml` | The console Deployment + Service (also the Prometheus target) |
| `31-review-console-ingress.yaml` | Public access — **not applied by kustomize**, read its header |
| `40-` … `42-` | Prometheus, Alertmanager, Grafana |
| `../monitoring/prometheus.yml` | The cluster's scrape config (one line differs from Compose's) |

`enrichment` and `ingestion` stay CLI-only, run the way Compose runs them
(`kubectl run --rm -it --image=<registry>/pcdf-ingestion:latest -- ingest ...`),
because nothing here needs them running continuously.

## Status

**Deployed and verified on AKS on 2026-08-25** -- single node,
`Standard_D4as_v5`, southindia, via `deploy/azure-trial`. A five-row company CSV
was dropped into a watched feed and came out the far end: 5 raw records, 25
observations (5 rows x 5 columns -- every cell captured), 4 entities because the
duplicate was linked rather than duplicated, and 22 golden values with the
competing values kept beside the winners. All twelve pods Running, all seven
PersistentVolumeClaims Bound, all three Prometheus targets up, and a manually
triggered backup produced an archive `pg_restore --list` read back with 26
tables of data.

Five things were wrong, and only a real cluster would have shown any of them.
All are fixed in the manifests. None was Azure-specific -- every one would have
failed the same way on any Kubernetes cluster:

- **The secret mount blocked the service-account token.** The secret volume
  mounts at `/run/secrets`, and `/var/run` is a symlink to `/run`, so
  Kubernetes' automatic token mount at
  `/var/run/secrets/kubernetes.io/serviceaccount` had to create a directory
  inside a read-only mount. Every pod mounting a secret failed to start with
  `mkdirat ... read-only file system`, before any application code ran, so
  there was nothing in the logs to explain it. Fixed with
  `automountServiceAccountToken: false`, which is right on its own terms too:
  nothing here calls the Kubernetes API.
- **Kafka refused to start on `lost+found`.** A PersistentVolume arrives as a
  freshly formatted ext4 filesystem, and every ext4 filesystem has a
  `lost+found` at its root. Kafka requires its log directory to contain nothing
  but topic data. Fixed by pointing `KAFKA_LOG_DIRS` at a subdirectory of the
  mount. Compose never hits this, because a Docker volume is a directory on an
  existing filesystem rather than a filesystem of its own.
- **Kafka could not resolve its own controller.**
  `KAFKA_CONTROLLER_QUORUM_VOTERS` names the broker as `kafka-0.kafka`, a
  per-pod DNS name that exists only when the governing Service is headless --
  and the Service was ClusterIP. Making it headless then exposed the second
  half: a headless Service publishes no DNS for pods that are not Ready, and the
  broker cannot become Ready until that name resolves.
  `publishNotReadyAddresses: true` breaks the deadlock.
- **The shared data volume was unwritable.** The images run as uid 10001 and a
  PersistentVolume arrives owned by root, so the watcher could not create the
  directory it exists to watch. Fixed with `fsGroup: 10001` on the two pods that
  write `/data`. Compose never hits this either -- a bind mount inherits the
  ownership of a host directory the user already owns.
- **The migrate Job gave up too early.** `backoffLimit: 3` expires about a
  minute in, which on a cold cluster is while Postgres is still attaching its
  disk and running initdb. The Job went permanently `Failed` and had to be
  deleted and recreated by hand. Raised to 10.

Still unverified: anything involving a *second* node. Postgres failover does not
exist (single instance, unchanged from ADR 0016), the ReadWriteMany path was
deliberately not exercised because the trial overlay replaces it with
ReadWriteOnce, and the backup CronJob has only been run on demand, never on its
own 02:00 schedule.

## The one storage decision

`pcdf-data` is **ReadWriteMany**, unlike every other volume here. Two pods
write it: the watcher sweeps `/data/inbox/watch`, and the console's upload form
writes into `/data/inbox/uploads`, so a file dropped through the browser lands
in the same tree as a file dropped on disk. Block storage cannot do that
across nodes, so this needs a filesystem-backed class — EFS, NFS, CephFS, or
(what this base runs) its own NFS server pod
([`09-nfs-server.yaml`](base/09-nfs-server.yaml)), statically bound to
`pcdf-data` by [`12-data-pvc.yaml`](base/12-data-pvc.yaml). See
`docs/decisions/0023-remove-aks-and-acr-dependency.md` for why: a
cloud-managed filesystem class (this used to be `azurefile-csi` on AKS) is
exactly the kind of dependency that disappears when the cluster is
self-managed rather than a managed Kubernetes service.

If your cluster has no ReadWriteMany class and you'd rather not run the
bundled NFS server, the fallback is to schedule the watcher and the console
onto the same node (`podAffinity`) and drop the claim back to
ReadWriteOnce — a single-node constraint, but a working one.

## Prerequisites

- A cluster (`kubectl cluster-info` succeeds), with a default StorageClass and
  a ReadWriteMany one.
- A container registry the cluster can pull from.
- `kubectl` >= 1.27 (for `kubectl apply -k`).

## Deploy sequence

```bash
# 1. Build and push all eight images. Same Dockerfiles Compose builds; only
#    the destination is new. Nothing cloud-specific in this script.
REGISTRY=myregistry.example.com sh infra/scripts/build-and-push.sh

# 2. Point an overlay at that registry. Copy deploy/azure/ if you are
#    somewhere else, and edit its images: block.

# 3. Namespace and secrets first. The Secret is never applied from
#    02-secret.example.yaml, which is a template, not a value.
kubectl apply -f infra/k8s/base/00-namespace.yaml
# Note the `tr -d '
'`. On Git Bash and other Windows shells openssl emits
# CRLF, and the carriage return silently becomes part of the password. Postgres
# initialises with it, the application reads the same file and agrees, and
# everything works -- until something receives the value through an environment
# variable instead of the file, which trims it, and authentication fails with no
# clue as to why. The backup CronJob is exactly that something.
rnd() { openssl rand "$@" | tr -d '
'; }
kubectl create secret generic pcdf-secrets -n pcdf \
  --from-literal=postgres_password="$(rnd -base64 24)" \
  --from-literal=grafana_admin_password="$(rnd -base64 24)" \
  --from-literal=pcdf_api_keys="$(rnd -hex 32),$(rnd -hex 32)"

# 4. Everything else.
kubectl apply -k deploy/azure

# 5. Migrations run once, as a Job. Wait for it. The consumers will crash-loop
#    against a schema that does not exist yet, which is noisy but harmless:
#    they recover on their own once this completes.
kubectl wait -n pcdf --for=condition=complete job/migrate --timeout=300s

# 6. Watch it settle. Postgres and Kafka come up first; the rest follows.
kubectl get pods -n pcdf -w
```

The data volume starts empty, so there are no feed directories yet: the watcher
logs `/data/inbox/watch does not exist` and sweeps nothing until one exists. One
directory per feed, each with its own `feed.json` (see
[`data/inbox/watch/README.md`](../../data/inbox/watch/README.md)):

```bash
WP=$(kubectl get pod -n pcdf -l app=watcher -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n pcdf "$WP" -- mkdir -p /data/inbox/watch/my-feed
kubectl exec -i -n pcdf "$WP" -- sh -c 'cat > /data/inbox/watch/my-feed/feed.json'   < data/inbox/watch/example/feed.json

# Then drop a file in. kubectl cp mangles Windows paths; a pipe avoids it.
cat some-vendor-export.csv   | kubectl exec -i -n pcdf "$WP" -- sh -c 'cat > /data/inbox/watch/my-feed/export.csv'
```

Read the API keys back out when you need to call the console:

```bash
kubectl get secret pcdf-secrets -n pcdf -o jsonpath='{.data.pcdf_api_keys}' | base64 -d
```

## Reaching it

```bash
kubectl port-forward -n pcdf svc/review-console 8000:8000   # /admin/page
kubectl port-forward -n pcdf svc/grafana 3000:3000
kubectl port-forward -n pcdf svc/prometheus 9090:9090
kubectl port-forward -n pcdf svc/alertmanager 9093:9093
```

Permanent public access to the console is `31-review-console-ingress.yaml`,
deliberately not applied by kustomize. The console serves personal data with
full provenance — names, emails, raw quarantined rows, which vendor sold them —
so read its header before applying it. It does fail closed: with no
`pcdf_api_keys` configured the console answers loopback only, and through an
Ingress every request arrives from somewhere else, so every request is refused
with 503.

## Secrets

Every setting can arrive as `NAME`, `NAME_FILE`, or a file at
`/run/secrets/<name>` — read without being asked to
([`libs/common/src/common/config.py`](../../libs/common/src/common/config.py)).
The Secret's keys are mounted at exactly that path on every pod that needs
them, the same seam `docker-compose.secrets.yml` already uses for Compose. No
application code changes between the two — that was the point of building the
seam that way in
[ADR 0015](../../docs/decisions/0015-operable-by-someone-else.md).

### Rotating a credential after first boot

Changing a value in the Secret is not enough for either of the two components
that store it themselves.

**Postgres** reads `POSTGRES_PASSWORD_FILE` only during `initdb`, on the very
first start with an empty volume. Afterwards the password lives in the database:

```bash
kubectl exec -n pcdf postgres-0 -- psql -U pcdf_dev -d pcdf   -c "ALTER USER pcdf_dev PASSWORD 'the-new-value';"
# then update the Secret to match, and restart everything that reads it
kubectl rollout restart deploy -n pcdf
```

**Grafana** reads `GF_SECURITY_ADMIN_PASSWORD` only when it first creates
`grafana.db`. Afterwards the password lives on the PVC, and a changed Secret is
silently ignored -- the symptom is "Invalid username or password" while holding
what is demonstrably the right value:

```bash
GP=$(kubectl get pod -n pcdf -l app=grafana -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n pcdf "$GP" -- grafana cli --homepath /usr/share/grafana   admin reset-admin-password 'the-new-value'
```

The consumers, watcher and console have no such problem: they read the mounted
file at startup, so a restart is all they need.

## Backups

`14-postgres-backup.yaml` runs `pg_dump` nightly at 02:00 UTC, verifies the
archive is readable with `pg_restore --list` before reporting success, and
keeps fourteen. **The dumps land on a volume in the same cluster as the
database they came from** — copy them somewhere else before calling this
disaster recovery:

```bash
kubectl get pods -n pcdf -l job-name --field-selector=status.phase=Succeeded
kubectl cp -n pcdf <backup-pod>:/backups/pcdf-<stamp>.dump ./restore-me.dump
```

## Running the ops scripts

`tools/ops/reblock.py`, `rebuild_golden.py`, `purge_source.py` and
`project_serving.py` are baked into the `pcdf-watcher` image at
`/app/tools/ops/` (see the comment in `services/record_pipeline/Dockerfile`),
and `tools/sql/` is baked into the console image at `/tools/` for the
Operations tab. Run one against the cluster with:

```bash
kubectl exec -n pcdf deploy/watcher -- \
  uv run --frozen --no-sync python /app/tools/ops/reblock.py --entity-type company
```

## On Azure specifically

[`deploy/azure/terraform`](../../deploy/azure/terraform/) provisions three
plain VMs — a k3s server ("1 master") and two k3s agents ("2 worker
nodes") — and nothing that is an Azure Kubernetes service. No AKS, no ACR;
see `docs/decisions/0023-remove-aks-and-acr-dependency.md` for why. **It
spends money from the moment it's applied.** Its defaults are B-series
(burstable): one `Standard_B2s` master (2 vCPU, 4 GiB) and two
`Standard_B1ms` workers (1 vCPU, 2 GiB each) — 4 vCPU total, sized to fit
an Azure free trial's regional quota outright. `terraform plan` shows
exactly what will be created before anything is; see [that directory's
README](../../deploy/azure/terraform/README.md) for the full sequence.
Delete everything it made with:

```bash
terraform -chdir=deploy/azure/terraform destroy
```

The overlay it pairs with,
[`deploy/azure/kustomization.yaml`](../../deploy/azure/kustomization.yaml),
changes exactly one thing about the base now: the registry the images come
from (ghcr.io). Storage no longer needs an overlay patch at all — the base
runs its own NFS server (see "The one storage decision" above).

### On trial credit

An Azure free-trial subscription is capped at **4 vCPUs per region**. The
module's default shape (1 master `B2s` + 2 workers `B1ms` = 2+1+1) fits that
exactly, so the real 1-master/2-worker architecture is testable on trial
credit with no `worker_count` workaround needed. B-series is burstable,
though — it throttles CPU under sustained load rather than sizing like the
old `D4as_v5` did, so this shape is for testing the architecture, not for
production load.

Apply [`deploy/azure-trial`](../../deploy/azure-trial/) instead of
`deploy/azure` on a single-node cluster. It layers two changes on top:

- **`pcdf-data` goes back to ReadWriteOnce on `managed-csi`.** With one node
  the watcher and the console are on the same node by definition, so the
  ReadWriteMany requirement — and Azure Files with it — disappears. This is the
  fallback described above, made concrete.
- **Every workload gets CPU and memory requests and limits.** Absent them, each
  pod is BestEffort, and BestEffort is what the kubelet evicts first when 16 GiB
  runs short. Totals: 1850m CPU and 4.4 GiB requested across fourteen
  workloads, against roughly 3.8 vCPU and 12.8 GiB allocatable once kube-system
  has taken its share.

What this gives up: any high availability at all. One node means a reboot is a
full outage, and going to two nodes means putting the ReadWriteMany class back.
It is a shape for proving the system on trial credit, not for serving anyone.

Stop paying for the node between sessions without losing a byte — the disks and
their contents survive:

```bash
az aks stop  --name pcdf-aks --resource-group pcdf
az aks start --name pcdf-aks --resource-group pcdf
```
