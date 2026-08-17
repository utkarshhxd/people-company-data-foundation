-- Keep the source file's column order, because jsonb does not.
--
-- normalization.repository.batch_columns recovered a batch's column list by
-- reading the keys of its first record's raw_payload, with the comment:
--
--     "Column order comes from the first record's payload, which preserves
--      file order."
--
-- That is not true of jsonb. jsonb stores object keys in its own order -- by
-- length, then bytewise -- so a file whose columns are
--
--     First Name, Last Name, Title, Company, ...
--
-- comes back as
--
--     City, Email, Lists, Stage, ...
--
-- Nothing depended on the order being right until two code paths derived a
-- column_fingerprint from two different sources of it:
--
--   * the batch stages fingerprint what batch_columns returns  (alphabetical)
--   * the record-at-a-time path fingerprints what the reader returns (file order)
--
-- Each is internally consistent, which is why both worked and neither
-- complained. But they produce different fingerprints for the same layout, so
-- the same file processed by each path creates two source_schema rows and two
-- independent sets of column_mapping.
--
-- The cost is not corrupt data; it is that a human correction stops being
-- reused. Approving a mapping on one path leaves the other path still asking,
-- which is precisely the work the source_schema table exists to avoid -- and it
-- is invisible, because both paths look right on their own.
--
-- So the order is stored explicitly, by whoever read the file, rather than
-- reconstructed afterwards from a structure that never promised to keep it.

ALTER TABLE batch ADD COLUMN columns jsonb;

COMMENT ON COLUMN batch.columns IS
    'Source column names in file order, as the reader saw them. Authoritative: '
    'raw_payload is jsonb and does not preserve key order.';

-- Existing batches keep a NULL here and fall back to the old behaviour. Their
-- order cannot be recovered -- it was never stored -- and rewriting it from the
-- schema they happen to point at would be a guess dressed as a repair.
