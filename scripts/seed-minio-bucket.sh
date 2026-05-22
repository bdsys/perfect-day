#!/usr/bin/env bash
# seed-minio-bucket.sh — idempotently creates the 'photos' bucket in the local MinIO instance
set -euo pipefail

ENDPOINT="${S3_ENDPOINT_URL:-http://localhost:9000}"
ACCESS_KEY="${S3_ACCESS_KEY:-minioadmin}"
SECRET_KEY="${S3_SECRET_KEY:-minioadmin}"
BUCKET="${S3_BUCKET_PHOTOS:-photos}"

echo "Seeding MinIO bucket '${BUCKET}' at ${ENDPOINT}..."

# Use mc (MinIO client) if available, else fall back to the MinIO mc Docker image
if command -v mc &>/dev/null; then
  mc alias set local "${ENDPOINT}" "${ACCESS_KEY}" "${SECRET_KEY}" --api S3v4 > /dev/null
  mc mb --ignore-existing "local/${BUCKET}"
  echo "✓ Bucket '${BUCKET}' ready"
else
  docker run --rm --network host \
    -e MC_HOST_local="${ACCESS_KEY}:${SECRET_KEY}@${ENDPOINT#http://}" \
    minio/mc mb --ignore-existing "local/${BUCKET}"
  echo "✓ Bucket '${BUCKET}' ready (via Docker)"
fi
