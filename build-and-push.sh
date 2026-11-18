#!/usr/bin/env bash
#
# Build and push the extron-exporter Docker image (linux/amd64 + linux/arm64)
# to GitHub Container Registry as ghcr.io/avxugent/extron-exporter.
#
# Requires: docker buildx, and `docker login ghcr.io` already done (a PAT with
# write:packages) for an account with push access to ghcr.io/avxugent/extron-exporter.
#
# Usage:
#   ./build-and-push.sh            # tags :latest and :<git-short-sha>
#   ./build-and-push.sh v1.2.0     # also tags :v1.2.0

set -euo pipefail

IMAGE="ghcr.io/avxugent/extron-exporter"
PLATFORMS="linux/amd64,linux/arm64"
BUILDER="extron-exporter-builder"

cd "$(dirname "${BASH_SOURCE[0]}")"

sha="$(git rev-parse --short HEAD)"
tags=(-t "${IMAGE}:latest" -t "${IMAGE}:${sha}")
if [[ $# -ge 1 ]]; then
  tags+=(-t "${IMAGE}:${1}")
fi

if ! docker buildx inspect "$BUILDER" >/dev/null 2>&1; then
  echo "Creating buildx builder '${BUILDER}' (docker-container driver, required for multi-platform push)..."
  docker buildx create --name "$BUILDER" --driver docker-container --bootstrap
fi

echo "Building and pushing ${IMAGE} for ${PLATFORMS}..."
docker buildx build \
  --builder "$BUILDER" \
  --platform "$PLATFORMS" \
  "${tags[@]}" \
  --push \
  .

echo "Done: ${tags[*]}"
