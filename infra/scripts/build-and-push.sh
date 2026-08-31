#!/bin/sh
# Build every image the cluster runs, and push them to a registry.
#
#     REGISTRY=myregistry.example.com/pcdf sh infra/scripts/build-and-push.sh
#     REGISTRY=... TAG=2026-08-25 sh infra/scripts/build-and-push.sh
#
# Same Dockerfiles `docker compose build` already uses; only the destination is
# new. Nothing here is cloud-specific -- REGISTRY is a hostname, and Docker Hub,
# a self-hosted Harbor, GHCR and a cloud registry all take the same two
# commands.
#
# Run from the repository root: every Dockerfile expects the whole workspace as
# its build context (they COPY libs/ and several services/*/pyproject.toml to
# get the dependency layer to cache).

set -eu

if [ -z "${REGISTRY:-}" ]; then
    echo "REGISTRY is not set. Example:" >&2
    echo "  REGISTRY=myregistry.azurecr.io sh infra/scripts/build-and-push.sh" >&2
    exit 1
fi

TAG="${TAG:-latest}"

# image name -> Dockerfile. The five stage consumers, the migrate Job's image
# (ingestion), the watcher, and the console. `enrichment` is absent on purpose:
# it is a CLI run inside a batch window, not something the cluster runs
# continuously -- run it with `kubectl run` from the ingestion image's sibling
# when you need it, the same way Compose runs it with `docker compose run`.
build() {
    name="$1"
    dockerfile="$2"
    echo ""
    echo "==> $REGISTRY/$name:$TAG  ($dockerfile)"
    docker build -f "$dockerfile" -t "$REGISTRY/$name:$TAG" .
    docker push "$REGISTRY/$name:$TAG"
}

build pcdf-ingestion      services/ingestion/Dockerfile
build pcdf-watcher        services/record_pipeline/Dockerfile
build pcdf-mapping        services/mapping/Dockerfile
build pcdf-normalization  services/normalization/Dockerfile
build pcdf-validation     services/validation/Dockerfile
build pcdf-resolution     services/resolution/Dockerfile
build pcdf-golden         services/golden/Dockerfile
build pcdf-review-console services/review_console/Dockerfile

echo ""
echo "Pushed 8 images to $REGISTRY at tag $TAG."
echo ""
echo "If TAG is not 'latest', tell kustomize:"
echo "  cd deploy/<your-overlay> && kustomize edit set image \\"
echo "    REGISTRY/pcdf-watcher=$REGISTRY/pcdf-watcher:$TAG   # ...and the other seven"
