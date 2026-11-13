"""Stream an S3 bucket to/from a tar archive (backup.sh / restore.sh). Runs inside the api image, which has
boto3 and reads the S3_* settings from its environment. Current object versions only.

    python -c "$(cat s3_tar.py)" dump         > objects.tar   (tar on stdout, summary JSON on stderr)
    python -c "$(cat s3_tar.py)" load BUCKET  < objects.tar   (creates BUCKET with versioning + SSE-S3)
    python -c "$(cat s3_tar.py)" init                         (provision S3_BUCKET; run with the S3_ADMIN_* identity)
"""

import json
import sys
import tarfile

from rebate.core.config import Settings
from rebate.modules.files import storage


def dump(settings: Settings) -> None:
    s3 = storage.client(settings)
    count = size = 0
    with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as tar:
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=settings.s3_bucket):
            for item in page.get("Contents", []):
                body = s3.get_object(Bucket=settings.s3_bucket, Key=item["Key"])["Body"]
                info = tarfile.TarInfo(item["Key"])
                info.size = item["Size"]
                tar.addfile(info, body)
                count, size = count + 1, size + item["Size"]
    print(json.dumps({"bucket": settings.s3_bucket, "objects": count, "bytes": size}), file=sys.stderr)


def load(settings: Settings) -> None:
    storage.ensure_bucket(settings)
    s3 = storage.client(settings)
    count = size = 0
    with tarfile.open(fileobj=sys.stdin.buffer, mode="r|") as tar:
        for member in tar:
            f = tar.extractfile(member)
            if f is None:
                continue
            s3.put_object(Bucket=settings.s3_bucket, Key=member.name, Body=f.read(), **storage.SSE)
            count, size = count + 1, size + member.size
    print(json.dumps({"bucket": settings.s3_bucket, "objects": count, "bytes": size}), file=sys.stderr)


mode = sys.argv[1]
if mode == "dump":
    dump(Settings())
elif mode == "load":
    load(Settings(s3_bucket=sys.argv[2]))
elif mode == "init":
    storage.ensure_bucket(Settings())
else:
    sys.exit("usage: dump | init | load BUCKET")
