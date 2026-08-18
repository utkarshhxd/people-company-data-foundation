#!/bin/sh
# Load a set of real vendor files end to end, one after another.
#
# Sequential on purpose. Resolution reads the entities every earlier record
# built, so records must go through in a defined order for the run to be
# reproducible -- and two loads running at once would also contend for the same
# entity rows. Each file is its own batch and its own transaction boundary; if
# one fails the ones before it stay exactly as they landed.
#
# Run from the repo root:
#     docker compose run --rm -v "${PWD}/testfiles:/tf:ro" pipeline sh /tools/ops/load_testfiles.sh

set -u

run() {
  file="$1"; entity="$2"; source_name="$3"; reliability="$4"
  echo ""
  echo "================================================================"
  echo "  $source_name  <-  $file"
  echo "================================================================"
  start=$(date +%s)
  # Not `set -e`: a file that fails must not stop the ones after it. Each run
  # already reports its own exit code, and a partial load leaves its batch
  # un-completed and therefore inert downstream.
  uv run --frozen --no-sync process run "/tf/$file" \
      --entity-type "$entity" \
      --source-name "$source_name" \
      --reliability "$reliability"
  code=$?
  echo "[$source_name] exit=$code elapsed=$(( $(date +%s) - start ))s"
}

run "Kopie van General Contractors-All US Markets.csv" company leadgen_markets 0.60
run "ME.csv"                                           company us_business_directory 0.70
run "DC.csv"                                           company us_business_directory 0.70
run "USA Consumers File4(93K).xlsx"                    person  usa_consumers 0.50
run "c_suite__director__founder_1760031638155_part30.csv" person apollo_c_suite 0.80

echo ""
echo "all files attempted"
