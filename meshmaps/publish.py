"""Uploading to R2 over its S3 API.

Credentials come from the environment and are never written anywhere:

    R2_ACCOUNT_ID         the Cloudflare account id (the endpoint's hostname)
    R2_ACCESS_KEY_ID      an R2 API token's access key, scoped to the one bucket
    R2_SECRET_ACCESS_KEY  its secret
    R2_BUCKET             defaults to meshclient-maps
"""

import json
import os

from . import catalog

IMMUTABLE = "public, max-age=31536000, immutable"
# Short enough that a new pack shows up within minutes. A client fetches the catalog when the
# download screen opens, not on a timer, so this is not a load concern.
CATALOG_CACHE = "public, max-age=300"


class PublishError(Exception):
    pass


def client():
    try:
        import boto3
        from botocore.config import Config
    except ImportError as error:
        raise PublishError("boto3 is not installed: pip install -e '.[publish]'") from error
    missing = [name for name in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY") if not os.environ.get(name)]
    if missing:
        raise PublishError(f"missing {', '.join(missing)}")
    return boto3.client(
        "s3",
        endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        # Recent botocore sends checksum headers by default; R2 does not need them.
        config=Config(request_checksum_calculation="when_required", response_checksum_validation="when_required"),
    )


def bucket():
    return os.environ.get("R2_BUCKET", "meshclient-maps")


def _key(relative):
    return f"{catalog.PREFIX}/{relative}"


def upload_pack(s3, pack_path, sidecar):
    """The pack first, then its sidecar, so no sidecar ever points at a pack that is not there."""
    s3.upload_file(
        pack_path,
        bucket(),
        _key(sidecar["url"]),
        ExtraArgs={"ContentType": "application/octet-stream", "CacheControl": IMMUTABLE},
    )
    s3.put_object(
        Bucket=bucket(),
        Key=_key(catalog.sidecar_key(sidecar["id"], sidecar["cut"], sidecar["style"])),
        Body=json.dumps(sidecar).encode(),
        ContentType="application/json",
        CacheControl=IMMUTABLE,
    )


def remote_sidecars(s3):
    found = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket(), Prefix=_key("packs/")):
        for item in page.get("Contents", []):
            if item["Key"].endswith(".json"):
                body = s3.get_object(Bucket=bucket(), Key=item["Key"])["Body"].read()
                found.append(json.loads(body))
    return found


def upload_catalog(s3, text):
    s3.put_object(
        Bucket=bucket(),
        Key=_key("catalog.json"),
        Body=text.encode(),
        ContentType="application/json; charset=utf-8",
        CacheControl=CATALOG_CACHE,
    )


def prune(s3, keep, *, dry_run):
    """Deletes all but the newest ``keep`` cuts of each (region, style). Returns the keys.

    Keeping more than one means a client halfway through downloading the previous cut can
    still finish it after a new one is published.
    """
    if keep < 2:
        raise PublishError("keep at least 2: a client may be mid-download of the previous cut")
    by_slot = {}
    for entry in remote_sidecars(s3):
        by_slot.setdefault((entry["id"], entry["style"]), []).append(entry)
    doomed = []
    for entries in by_slot.values():
        entries.sort(key=lambda entry: entry["cut"], reverse=True)
        for entry in entries[keep:]:
            doomed.append(_key(catalog.sidecar_key(entry["id"], entry["cut"], entry["style"])))
            doomed.append(_key(entry["url"]))
    if not dry_run:
        for key in doomed:
            s3.delete_object(Bucket=bucket(), Key=key)
    return doomed
