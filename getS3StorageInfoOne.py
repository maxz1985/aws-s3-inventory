import boto3
import datetime
import csv
from botocore.exceptions import ClientError

# -------------------- CONFIG --------------------

# AWS CLI profile for THIS account
# For SSO, log in first:
#   aws sso login --profile my-account-profile
PROFILE = "my-account-profile"

# How far back to look in CloudWatch for a datapoint
DAYS_LOOKBACK = 3

# Output CSV file
CSV_FILENAME = "s3_inventory_single_account.csv"

# Storage types to query (adjust if you don't care about some)
STORAGE_TYPES = [
    "StandardStorage",
    "StandardIAStorage",
    "OneZoneIAStorage",
    "ReducedRedundancyStorage",
    "GlacierStorage",
    "GlacierInstantRetrievalStorage",
    "GlacierDeepArchiveStorage",
    "IntelligentTieringFAStorage",
    "IntelligentTieringIAStorage",
    "IntelligentTieringAAStorage",
]

# ------------------------------------------------
# Base session & clients using the chosen profile
# ------------------------------------------------

base_session = boto3.Session(profile_name=PROFILE)
s3_client = base_session.client("s3")
sts_client = base_session.client("sts")


def get_account_id():
    """Return the current account ID from STS."""
    resp = sts_client.get_caller_identity()
    return resp["Account"]


def get_bucket_region(s3_client, bucket_name):
    """Get the region of an S3 bucket (handles the us-east-1 quirk)."""
    try:
        resp = s3_client.get_bucket_location(Bucket=bucket_name)
        loc = resp.get("LocationConstraint")
        # S3 quirk: us-east-1 is returned as None
        return "us-east-1" if loc is None else loc
    except ClientError as e:
        print(f"  [WARN] get_bucket_location failed for {bucket_name}: {e}")
        return None


def get_bucket_size_bytes(cw_client, bucket_name):
    """
    Return a dict of {storage_type: size_in_bytes} for the bucket,
    using CloudWatch AWS/S3 BucketSizeBytes metric.
    """
    end_time = datetime.datetime.utcnow()
    start_time = end_time - datetime.timedelta(days=DAYS_LOOKBACK)

    sizes = {}

    for stype in STORAGE_TYPES:
        try:
            resp = cw_client.get_metric_statistics(
                Namespace="AWS/S3",
                MetricName="BucketSizeBytes",
                Dimensions=[
                    {"Name": "BucketName", "Value": bucket_name},
                    {"Name": "StorageType", "Value": stype},
                ],
                StartTime=start_time,
                EndTime=end_time,
                Period=86400,  # 1 day
                Statistics=["Average"],
            )
            datapoints = resp.get("Datapoints", [])
            if not datapoints:
                continue

            latest = max(datapoints, key=lambda d: d["Timestamp"])
            sizes[stype] = latest["Average"]
        except ClientError as e:
            print(f"    [WARN] CW metric error bucket={bucket_name} type={stype}: {e}")

    return sizes

def get_bucket_tags(s3_client, bucket_name):
    """
    Return bucket tags as a dict {key: value}.
    Returns empty dict if bucket has no tags.
    """
    try:
        resp = s3_client.get_bucket_tagging(Bucket=bucket_name)
        return {t["Key"]: t["Value"] for t in resp.get("TagSet", [])}
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchTagSet":
            return {}
        print(f"  [WARN] get_bucket_tagging failed for {bucket_name}: {e}")
        return {}


def write_results_to_csv(results, filename):
    """Write the aggregated results to CSV."""
    # Collect all storage-type keys that appeared in any result
    all_storage_types = set()
    for r in results:
        all_storage_types.update(r["by_storage_type"].keys())
    storage_type_columns = sorted(all_storage_types)

    fieldnames = ["account_id", "bucket_name", "region", "total_bytes", "tags"] + storage_type_columns

    with open(filename, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for r in results:
            row = {
                "account_id": r["account_id"],
                "bucket_name": r["bucket_name"],
                "region": r["region"],
                "total_bytes": int(r["total_bytes"]),
                "tags": ";".join(
                    f"{k}={v}" for k, v in sorted(r["tags"].items())
                ),
            }
            for stype in storage_type_columns:
                row[stype] = int(r["by_storage_type"].get(stype, 0))
            writer.writerow(row)

    print(f"\nWrote {len(results)} rows to {filename}")


def main():
    account_id = get_account_id()
    print(f"Running in account {account_id}")

    results = []

    try:
        buckets_resp = s3_client.list_buckets()
    except ClientError as e:
        print(f"[ERROR] list_buckets failed: {e}")
        return

    buckets = buckets_resp.get("Buckets", [])
    print(f"Found {len(buckets)} buckets")

    for b in buckets:
        bucket_name = b["Name"]
        region = get_bucket_region(s3_client, bucket_name)
        if not region:
            continue

        print(f"\nBucket: {bucket_name} (region: {region})")

        # CloudWatch client must be in the bucket's region
        cw_client = base_session.client("cloudwatch", region_name=region)

        sizes_by_type = get_bucket_size_bytes(cw_client, bucket_name)
        total_bytes = sum(sizes_by_type.values())

        tags = get_bucket_tags(s3_client, bucket_name)

        print(f"  Total size (bytes): {int(total_bytes)}")

        results.append({
            "account_id": account_id,
            "bucket_name": bucket_name,
            "region": region,
            "total_bytes": total_bytes,
            "by_storage_type": sizes_by_type,
            "tags": tags,
        })

    write_results_to_csv(results, CSV_FILENAME)


if __name__ == "__main__":
    main()
