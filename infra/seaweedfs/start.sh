#!/bin/sh
# Dev/CI SeaweedFS (ADR-017): one S3 identity from the environment, SSE-S3 key via WEED_S3_SSE_KEY.
set -eu
: "${S3_ACCESS_KEY_ID:?}" "${S3_SECRET_ACCESS_KEY:?}" "${WEED_S3_SSE_KEY:?}"
umask 077
# Dev/CI (S3_SCOPE_BUCKET unset): one identity with Admin so the worker can create the bucket.
# Production (S3_SCOPE_BUCKET=<bucket>): the app identity is confined to that bucket (Admin:<bucket> only covers
# versioning/encryption of it; it cannot create or delete other buckets or touch their objects), and a separate
# admin identity (S3_ADMIN_*) exists for out-of-band provisioning and restores. Verified against SeaweedFS 4.50.
if [ -n "${S3_SCOPE_BUCKET:-}" ]; then
  : "${S3_ADMIN_ACCESS_KEY_ID:?}" "${S3_ADMIN_SECRET_ACCESS_KEY:?}"
  b="$S3_SCOPE_BUCKET"
  cat > /tmp/s3.json <<JSON
{"identities":[
 {"name":"app","credentials":[{"accessKey":"${S3_ACCESS_KEY_ID}","secretKey":"${S3_SECRET_ACCESS_KEY}"}],
  "actions":["Admin:$b","Read:$b","Write:$b","List:$b","Tagging:$b"]},
 {"name":"ops","credentials":[{"accessKey":"${S3_ADMIN_ACCESS_KEY_ID}","secretKey":"${S3_ADMIN_SECRET_ACCESS_KEY}"}],
  "actions":["Admin","Read","Write","List","Tagging"]}]}
JSON
else
cat > /tmp/s3.json <<JSON
{"identities":[{"name":"app","credentials":[{"accessKey":"${S3_ACCESS_KEY_ID}","secretKey":"${S3_SECRET_ACCESS_KEY}"}],
  "actions":["Admin","Read","Write","List","Tagging"]}]}
JSON
fi
# Several buckets each reserve volumes: allow plenty, keep each small (dev/CI disks).
exec weed server -dir=/data -s3 -s3.port=8333 -s3.config=/tmp/s3.json -volume.max=100 -master.volumeSizeLimitMB=1024
