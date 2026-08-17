#!/bin/sh
# Load the bounded samples cut from the real vendor files.
#
# A sample of a layout proves everything about that layout that the whole file
# would: same columns, same conventions, same defects. What it does not prove is
# behaviour at volume -- that is what a full run is for, and it costs hours. The
# two questions are worth separating so the cheap one can be asked often.
#
# Source names deliberately match the full-file runs. The samples are subsets,
# so their records should link to entities those runs already built rather than
# duplicating them -- which makes idempotency part of what this exercises rather
# than something tested separately.
#
#     docker compose run --rm --entrypoint sh pipeline /tools/load_samples.sh

set -u
DIR=/data/inbox/scaled

run() {
  file="$1"; entity="$2"; source_name="$3"; reliability="$4"
  echo ""
  echo "=== $source_name  <-  $file"
  start=$(date +%s)
  uv run --frozen --no-sync process run "$DIR/$file" \
      --entity-type "$entity" --source-name "$source_name" \
      --reliability "$reliability"
  echo "[$source_name] exit=$? elapsed=$(( $(date +%s) - start ))s"
}

run kopie_van_general_contractors_all_us_markets__csv.csv    company leadgen_markets 0.60
run me__csv.csv                                              company us_business_directory 0.70
run dc__csv.csv                                              company us_business_directory 0.70
run usa_consumers_file4_93k__xlsx__sheet1.csv                person  usa_consumers 0.50
run c_suite_director_founder_1760031638155_part30__csv.csv   person  apollo_c_suite 0.80

echo ""
echo "all samples attempted"
