# ADR 0010: Increment 10 — pipeline metrics

## Status

Accepted.

## Context

Prometheus and Grafana were provisioned in increment 1 and had watched nothing
but process health ever since: is the API up, how many HTTP requests. None of
that says anything about whether the *data foundation* is healthy.

The failure modes this system actually has are quiet ones. A quarantined record
loses nothing by waiting — and nothing ages the queue, so a growing backlog is
invisible. An unreviewed mapping means real values are captured but attributed
to no field, and therefore never validated or resolved on. An open match
candidate means one real-world entity is being represented by two ids. All three
degrade the data silently while every container stays green.

## Decisions

- **Gauges queried from Postgres at scrape time, not counters incremented in
  each service.** The numbers worth watching are accumulated state — queue
  depths, dispute counts, coverage — and an in-process counter answers a
  different question badly: it resets on restart, and a Kafka replay
  double-counts work done once. A query against the source of truth is correct
  at every scrape regardless of what the services have been through.

  The cost is a handful of aggregate queries per scrape, which is why results
  are cached for 12s against a 15s scrape interval: at most one round of queries
  per scrape, never a value from the previous interval.

- **A failed scrape reports itself and serves anyway.** If Postgres is
  unreachable the endpoint still returns 200 with `pcdf_metrics_up 0` and no
  stale business gauges. Monitoring that dies with the database is useless at
  exactly the moment it is needed. Verified by stopping Postgres: `/metrics`
  kept serving, `pcdf_metrics_up` went to 0, HTTP metrics were unaffected, and
  `/health/live` stayed 200 because it is dependency-free by design.

- **Age metrics, not just counts.** `pcdf_oldest_open_quarantine_age_seconds`
  and its siblings are the genuinely operational numbers: a count of 3 is fine,
  a count of 3 that has been 3 for a fortnight is a process failure. This closes
  the gap ADR 0006 flagged — *"nothing ages an open item"*.

- **The API carries the gauges.** It is the only long-running HTTP process and
  Prometheus already scrapes it; a separate exporter container for a handful of
  queries would be more moving parts for no benefit. The trade-off is that
  pipeline metrics stop when the API is down — acceptable, since `up{job=...}`
  already covers that case and the dashboard shows both.

- **Metric definitions are data, not code.** Each is a `MetricSpec` with a name,
  documentation and SQL returning label values then the number. Adding a metric
  is one tuple entry, and the collector has one code path to get wrong.

- **Panel descriptions carry the reasoning.** Every Grafana panel says what the
  number means and what it does *not* mean — that `invalid` means "not safe to
  resolve yet", never deleted; that unpublished Kafka events are a visible gap
  to replay rather than data loss; that contested golden values are not errors.
  A dashboard read by someone who did not build the system needs the caveat next
  to the number, not in a document.

## Verification

Live scrape against the accumulated pipeline state:

```
pcdf_metrics_up 1.0
pcdf_batches{status="completed"} 7.0
pcdf_raw_records 33.0
pcdf_column_mappings{status="needs_review"} 2.0
pcdf_oldest_unreviewed_mapping_age_seconds 61678.86
pcdf_records_validated{status="valid"} 14.0
pcdf_records_validated{status="invalid"} 5.0
pcdf_quarantine_items{status="open"} 3.0
pcdf_oldest_open_quarantine_age_seconds 60523.33
pcdf_entities{entity_type="company",status="active"} 18.0
pcdf_entities{entity_type="company",status="merged"} 1.0
pcdf_golden_values{state="current"} 255.0
pcdf_golden_contested_values 9.0
pcdf_golden_mean_confidence{entity_type="company"} 0.667
```

Prometheus reports 20 distinct `pcdf_` series. Grafana provisions the
"Data Foundation" dashboard (18 panels) automatically.

Every number reconciles with the state built up over the previous increments:
5 invalid records against 3 open + 1 rejected + 1 released quarantine items, one
merged company tombstone from the accepted match candidate, 255 current golden
values with 5 superseded.

## Consequences

- The gauges are absolute state, so rate-based alerting needs care: a rising
  `pcdf_quarantine_items{status="open"}` is normal during ingestion and only
  concerning if it stays. The age metrics are the better alerting signal.
- No alert rules are defined. The dashboard makes the numbers visible; deciding
  the thresholds that should page someone is a judgement about how this
  foundation gets operated, not a technical one.
- `pcdf_golden_mean_confidence` averages across all fields, which mixes
  identifiers with volatile attributes. Useful as a trend, misleading as an
  absolute — a per-field breakdown would be a larger cardinality decision.
