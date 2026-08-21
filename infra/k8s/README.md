# Running this on more than one machine

Docker Compose assumes one host: every service reaches Postgres on a bridge
network only that host has, bind-mounts `./data` and `./tools` from a checkout
only that host has, and restarts a crashed container on the same node because
there is no other node. `infra/k8s/base/` is the same handful of pieces
translated to run across a cluster instead — orchestrated restarts, a stable
volume and name across reschedules, and no single machine whose disk is the
whole database's disk.

What's here: Postgres, the one-shot migration Job, and the watcher (the only
long-running thing in the record-at-a-time path — see the repo root README's
"Two processing paths" section). Ingestion, mapping, normalization,
validation, resolution and golden stay CLI-only, run the same way Compose runs
them (`docker compose run --rm <service> ...` / the cluster equivalent,
`kubectl run --rm -it --image=... -- <command>`), because nothing here needs
them running continuously.

**Status: written and rendered, not deployed.** No Kubernetes cluster was
reachable in the environment these manifests were built in (no `kubectl`
context, no `minikube`/`kind` binary, Docker Desktop's Kubernetes off). What
*has* been verified, and what has not, is below — read it before pointing
this at anything that matters.

## What was actually checked

- `kubectl kustomize infra` renders successfully: every resource is
  well-formed YAML and every generated ConfigMap reference resolves via
  kustomize's automatic reference rewriting.
- `services/record_pipeline/Dockerfile` bakes in `tools/` (it was
  bind-mounted read-only in Compose, which assumes a host with this
  repository checked out — not true once a pod can land on any node). The
  image was rebuilt locally and `python /app/tools/ops/reblock.py --help`
  ran inside it as the non-root `app` user, printing its full usage text.
- The command lines in `20-watcher.yaml` and `13-migrate-job.yaml` were run
  standalone against the images already built by `docker compose build`
  (`process watch --help`, `python -m common.migrations`): each reaches the
  Postgres connection attempt with no argument-parsing or import error,
  which is as far as a command can be exercised without the infrastructure
  it needs.

## What was not checked, because there was nothing to check it against

- **PersistentVolumeClaims actually binding.** Both PVCs here omit
  `storageClassName`, deliberately, so the cluster's default is used —
  but whether your cluster *has* a default (and whether it's ReadWriteOnce
  block storage or something slower) is unverified.
- **The images existing anywhere a cluster can pull them from.** Every
  Deployment/Job image is a placeholder (`pcdf-ingestion:local` etc.); the
  `images:` transformer at the bottom of `infra/kustomization.yaml` needs a
  real registry substituted for `REGISTRY` — see below.
- **Backup and restore.** `tools/ops/backup.sh` and `restore_check.sh` are
  written for `docker compose exec`. Their Kubernetes equivalent — a
  `CronJob` running `pg_dump` against the `postgres` Service — does not
  exist yet. Until it does, back up by hand: `kubectl exec
  statefulset/postgres -- pg_dump -U pcdf_dev pcdf > backup.sql`.
- **Postgres failover.** Still a single instance (`10-postgres.yaml`).
  Kubernetes gives it a stable volume and a stable name across
  reschedules, which Compose cannot, but a second pod would be a second,
  empty database, not a standby. Real HA needs streaming replication
  (Patroni, CloudNativePG) and is further future work, not something this
  pass added.

## Prerequisites

- A cluster (`kubectl cluster-info` succeeds) with a default StorageClass
  and enough capacity for the PersistentVolumeClaims in `12-data-pvc.yaml`.
- A container registry the cluster can pull from.
- `kubectl` >= 1.27 (for `kubectl kustomize` / `kubectl apply -k`).

## Deploy sequence

```bash
# 1. Build and push the images that actually run continuously or get invoked
#    as one-off CLIs. Same Dockerfiles Compose already builds; only the
#    destination is new.
for svc in ingestion mapping normalization validation resolution golden; do
  docker build -f services/$svc/Dockerfile -t $REGISTRY/pcdf-$svc:latest .
  docker push $REGISTRY/pcdf-$svc:latest
done
docker build -f services/record_pipeline/Dockerfile -t $REGISTRY/pcdf-watcher:latest .
docker push $REGISTRY/pcdf-watcher:latest

# 2. Point the kustomization at that registry.
sed -i "s#REGISTRY/#$REGISTRY/#g" infra/kustomization.yaml   # already there; edit newName by hand instead if you'd rather not sed

# 3. Namespace, config and secret first -- the Secret from a real value, not
#    from k8s/base/02-secret.example.yaml, which is a template.
kubectl apply -f infra/k8s/base/00-namespace.yaml
kubectl create secret generic pcdf-secrets -n pcdf \
  --from-literal=postgres_password='...'

# 4. Everything else, via kustomize.
kubectl apply -k infra

# 5. Migrations run once, as a Job -- wait for it before anything that reads
#    the schema comes up healthy.
kubectl wait -n pcdf --for=condition=complete job/migrate --timeout=120s
```

## Secrets

Every setting can arrive as `NAME`, `NAME_FILE`, or a file at
`/run/secrets/<name>` — read without being asked to
(`libs/common/src/common/config.py`). The `pcdf-secrets` Secret's
`postgres_password` key is mounted at exactly that path on every Deployment
that needs it, the same seam `docker-compose.secrets.yml` already uses for
Compose. No application code changes between the two — that was the point of
building the seam that way in
[ADR 0015](../../docs/decisions/0015-operable-by-someone-else.md).

## Running the ops scripts

`tools/ops/reblock.py`, `rebuild_golden.py` and `purge_source.py` are baked
into the `pcdf-watcher` image at `/app/tools/ops/` (see the comment in
`services/record_pipeline/Dockerfile`). Run one against the cluster with:

```bash
kubectl exec -n pcdf deploy/watcher -- \
  uv run --frozen --no-sync python /app/tools/ops/reblock.py --entity-type company
```
