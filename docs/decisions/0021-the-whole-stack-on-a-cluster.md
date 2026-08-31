# ADR 0021: The whole stack on a cluster, and no cloud underneath it

## Status

Accepted and deployed. Supersedes the Kafka and scope decisions in
[ADR 0016](0016-kubernetes-deployment.md); everything else in 0016 stands.

Unlike 0016, this one was run: a single-node AKS cluster on 2026-08-25, with a
file carried end to end through it. See Verification for what that did and did
not settle.

## Context

Two things were true when this started.

**ADR 0016 no longer described the tree.** It records ten Deployments, two
StatefulSets, a three-broker Kafka cluster and a
`settings.kafka_topic_replication_factor` replacing a hardcoded constant.
None of that is on disk. `infra/k8s/base/` held seven files — namespace,
ConfigMap, Secret template, Postgres, the data volume, the migrate Job, the
watcher — and `libs/common/src/common/kafka.py` still has
`TOPIC_REPLICATION_FACTOR = 1` as a constant. The manifests cover three of the
eleven things Compose runs. An ADR that overstates what exists is worse than no
ADR, because the next person reads it instead of the directory.

**The deployment target became a real one.** Not "some cluster, eventually" but
Azure, now. Which raises the question that decides the shape of everything
else: how much of this system should be Azure's?

The tempting answer is most of it. Azure sells a managed Postgres with
point-in-time restore, a managed Kafka endpoint, managed Grafana. Each one
removes a StatefulSet and some operational surface. Together they also make the
answer to "can we run this somewhere else" into a rewrite rather than a
redeploy — and this is a system whose entire premise is that data outlives the
thing displaying it (ADR 0003, ADR 0020). A pipeline that cannot be moved is a
strange thing to build under that premise.

## Decision

**Everything runs as pods. The cloud supplies a cluster and a registry, and
nothing else.** Postgres, Kafka, Prometheus, Alertmanager and Grafana are
containers in the cluster exactly as they are containers under Compose. No
managed database, no managed broker, no cloud-specific CRD, nothing in
`infra/` that names a vendor.

The cost of this is real and is accepted: Postgres backups, upgrades and
failover are ours, where a managed service would have made them somebody's
product. What is bought with it is that the deployment target is *Kubernetes*,
which every cloud sells and which also runs in a rack, rather than *Azure*.

**Azure is confined to `deploy/`.** `deploy/azure/` holds an overlay that
changes exactly two things about the base — the registry the images come from,
and `azurefile-csi` as the ReadWriteMany StorageClass for the shared data
volume — and a script that creates the AKS cluster and the ACR registry.
Deploying to GKE, EKS or a cluster on-premises means copying that directory and
changing those two things.

`deploy/azure-trial/` sits on top of it for the single-node case an Azure
free-trial subscription forces (4 vCPUs per region, which is one node). It
returns the shared volume to ReadWriteOnce — with one node, the two pods that
write it are co-located by definition, so the ReadWriteMany requirement is not
merely relaxed but genuinely absent — and adds the CPU and memory requests the
base leaves off. The base leaves them off deliberately: a request is a claim
about how much of *a particular cluster* a pod should hold, which the base
cannot know and an overlay can.

It sits under `deploy/` rather than `infra/overlays/` for a mechanical reason:
kustomize refuses a base that contains the overlay pointing at it ("cycle
detected"), and the base's root has to be `infra/` so it can read the Prometheus
rules and Grafana dashboards underneath it.

**The manifests now cover the whole stack**, not three pieces of it: the five
stage consumers, the review console, Prometheus, Alertmanager, Grafana, and a
nightly backup CronJob, alongside what was already there. `enrichment` and
`ingestion` stay CLI-only — they are invoked inside a window, not run
continuously, exactly as under Compose.

**Kafka goes back to one broker**, reversing ADR 0016. Three brokers were
written there and are not in the tree, and the constant they required is not in
the tree either. Rather than rebuild them, this records why one is defensible:
records do not move between stages on the broker — a record's whole turn
through the pipeline is one function call in one transaction (ADR 0012) — so
what a topic carries is an announcement that committed rows exist. Losing a
broker costs a re-announcement, not data. The single broker is also what
`TOPIC_REPLICATION_FACTOR = 1` already assumes, so the manifest and the library
now agree, which they did not before.

**The backup CronJob is built this time.** ADR 0016 named it as a gap and
deferred it. `pg_dump` nightly against the `postgres` Service, custom format,
then `pg_restore --list` over the result before reporting success — the same
policy as `tools/ops/backup.sh`, because a backup nobody has read back is not a
backup. The script itself cannot be reused verbatim: it lives in `tools/`,
kustomize will not read files outside its root, and the Postgres image has no
checkout of this repository. Its two steps are inlined in the CronJob instead,
and both files say so.

**`tools/` is baked into the console image too**, at `/tools`. ADR 0016 did
this for `record_pipeline` only. The console's Operations tab reads
`/tools/sql/verify.sql` from an absolute path, which under Compose is a bind
mount of a checkout that a scheduled pod does not have. Same path, so the
Compose bind mount still overlays identical content and nothing about that path
changes.

**The Ingress and the Secret are excluded from `resources:` on purpose.** The
console serves personal data with full provenance; applying an Ingress puts
that on a public IP. It does fail closed — with no `pcdf_api_keys` configured
the console answers loopback only, and every request through an Ingress arrives
from somewhere else — but "fails closed" is a property to rely on when
something goes wrong, not a reason to make exposure the default. Both files are
templates with headers explaining what to read before applying them by hand.

**Rejected: Azure Database for PostgreSQL.** It would have closed the backup,
patching and failover gaps this ADR leaves open, at roughly the price of the
StatefulSet it replaced. Rejected because it is the one dependency that would
be genuinely hard to leave: the data is the system.

**Rejected: Azure Event Hubs for Kafka.** Managed and protocol-compatible, but
its Kafka endpoint is SASL_SSL and every consumer here assumes PLAINTEXT, so it
is a code change disguised as a configuration change.

**Rejected: a Helm chart.** Same reasoning as ADR 0016, unchanged. The
variation between environments is a registry host and a StorageClass name;
kustomize overlays express that without introducing a templating language.

## Consequences

- The ReadWriteMany volume is the one genuinely awkward requirement. Two pods
  write `/data`: the watcher sweeps `inbox/watch`, and the console's upload
  form writes `inbox/uploads`. Block storage cannot do that across nodes, so a
  cluster without a filesystem-backed StorageClass needs the fallback in
  `infra/k8s/README.md` — co-schedule the two pods and drop back to
  ReadWriteOnce, which works but pins them to one node.
- Postgres backups now exist on a schedule but land on a volume in the same
  cluster as the database they came from. That is a backup, not disaster
  recovery, and `infra/k8s/README.md` says so where somebody will read it.
- Postgres failover is still not solved, unchanged from ADR 0016. Kubernetes
  buys a stable volume and a stable name across reschedules; a standby needs
  streaming replication and is still future work.
- Deploying means building and pushing eight images.
  `infra/scripts/build-and-push.sh` does it and names no cloud —
  `REGISTRY=` is a hostname, and Docker Hub, Harbor, GHCR and any cloud
  registry all take the same two commands.
- ADR 0016 should be read with this one. Its secrets seam, its "no Helm"
  reasoning and its account of what Compose's single-host assumption was
  load-bearing for are all still accurate; its Kafka section and its inventory
  of what is deployed are not.

## Verification

Rendered before deployment: `kubectl kustomize infra`, `deploy/azure` and
`deploy/azure-trial` all produce well-formed output, every generated ConfigMap's
hash-suffixed name appears both as the object and as the volume reference,
all eight images resolve through the overlay's transformer, the Secret template
and the Ingress stay out, and the `azurefile-csi` patch lands on `pcdf-data`
and nothing else.

Then run for real, on AKS, single `Standard_D4as_v5` node in southindia, via
`deploy/azure-trial`:

- All twelve pods Running, all seven PersistentVolumeClaims Bound, all three
  Prometheus targets `up`, console `/health/ready` returning
  `{"status":"ok","checks":{"postgres":"ok"}}` and `/metrics` reporting
  `pcdf_kafka_up 1`.
- 25 migrations applied by the Job.
- A five-row company CSV dropped into a watched feed: 5 raw records, 25
  attribute observations — 5 rows by 5 columns, every cell captured, which is
  ADR 0004's whole claim holding on a cluster — 4 entities, because the fifth
  row was linked to an existing company rather than duplicated, and 22 golden
  values with competing values (`employee_count` 240 against 245) preserved
  beside the winners rather than overwritten.
- A manually triggered backup produced an archive that `pg_restore --list` read
  back with 26 tables of data, which is the inlined script's whole point.

Five defects surfaced, none of them Azure-specific and none findable by
rendering: the secret mount at `/run/secrets` colliding with Kubernetes' own
service-account mount under `/var/run`; Kafka refusing to start on the
`lost+found` that every fresh ext4 volume carries; Kafka unable to resolve
`kafka-0.kafka` because the Service was not headless, then unable to resolve it
because headless Services hide not-ready pods; the `/data` volume arriving
root-owned against containers running as uid 10001; and the migrate Job's
`backoffLimit: 3` expiring while Postgres was still initialising. All five are
fixed in `infra/k8s/base/`, and `infra/k8s/README.md` describes each where an
operator will meet it.

A sixth was operational rather than structural, and is worth recording because
it wasted the most time: `openssl rand` on Git Bash emits CRLF, so a carriage
return ended up inside the Postgres password. The application read the password
from the mounted file and agreed with the database completely; only the backup
CronJob, which receives it through an environment variable that trims it,
failed — with `password authentication failed`, from the one component whose
failure is silent until the day it matters. The README's secret-creation snippet
now strips ``.

Not settled by any of this: anything requiring a second node. Postgres failover
still does not exist, the ReadWriteMany path is deliberately not exercised by
the trial overlay, and the backup CronJob has never run on its own schedule.
