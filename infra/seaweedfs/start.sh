#!/bin/sh
# Dev/CI SeaweedFS (ADR-017): one S3 identity from the environment, SSE-S3 key via WEED_S3_SSE_KEY.
set -eu
: "${S3_ACCESS_KEY_ID:?}" "${S3_SECRET_ACCESS_KEY:?}" "${WEED_S3_SSE_KEY:?}"
umask 077
cat > /tmp/s3.json <<JSON
{"identities":[{"name":"app","credentials":[{"accessKey":"${S3_ACCESS_KEY_ID}","secretKey":"${S3_SECRET_ACCESS_KEY}"}],
  "actions":["Admin","Read","Write","List","Tagging"]}]}
JSON
# Several buckets each reserve volumes: allow plenty, keep each small (dev/CI disks).
exec weed server -dir=/data -s3 -s3.port=8333 -s3.config=/tmp/s3.json -volume.max=100 -master.volumeSizeLimitMB=1024
