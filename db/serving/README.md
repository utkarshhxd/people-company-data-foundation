# The serving database

Everything in this directory is run against a database this repository does not
own: the application that displays leads to customers. It sits apart from
`db/migrations/` for that reason — the `migrate` job never touches it, and
nobody should be able to apply it by accident.

`0001_serving_contract.sql` is the contract. Apply it once, before the first
projection run:

```bash
psql "$SERVING_URL" -v ON_ERROR_STOP=1 -f db/serving/0001_serving_contract.sql
```

Take a backup first. The file narrows several text columns to numeric and date
types, and a value that will not parse becomes NULL. Every original string is
copied into `serving_pre_numeric_backup` before that happens, but a table in the
same database is not a backup.

## If it refuses to run

The first thing the file does is count duplicates:

```
ERROR: cannot apply the serving contract: 34 duplicated company domain(s)
       and 7 duplicated lead email(s) already exist.
```

Nothing has been changed at that point — the whole file is one transaction. The
serving schema indexed `domain` and lead emails without ever constraining them,
so the same company could arrive as many times as it was imported. Find them:

```sql
SELECT lower(domain), count(*), array_agg(company_id)
FROM companies WHERE domain IS NOT NULL AND btrim(domain) <> ''
GROUP BY 1 HAVING count(*) > 1 ORDER BY 2 DESC;

SELECT lower(email_address), count(*), array_agg(lead_id)
FROM leads WHERE email_address IS NOT NULL AND btrim(email_address) <> ''
GROUP BY 1 HAVING count(*) > 1 ORDER BY 2 DESC;
```

Merge or delete, then run the file again. Which survivor to keep is a decision
about that data and is deliberately not automated here.

## What it does not do

**It does not drop `users.credit_balance`.** That column and
`sum(user_credit_batches.amount_remaining)` are two homes for one number and
will drift. The batches are the ledger — only they can express expiry — so the
column is a cache of them, and the `user_credit_balance` view added by the
contract is where the two can be compared:

```sql
SELECT * FROM user_credit_balance WHERE abs(cached - available) > 0.005;
```

Removing the column is right, and it is the application repository's change to
make: application code reads it today.

**It does not migrate any data into `leads` or `companies`.** That is
`tools/ops/project_serving.py` — see
[the projection guide](../../docs/guides/serving-projection.md).

**It does not touch the pipeline database.** Nothing here is reversed by
`tools/ops/restore_check.sh` or included in `tools/ops/backup.sh`; the serving
database is backed up by whoever runs the application.
