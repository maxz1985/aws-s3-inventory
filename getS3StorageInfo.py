import boto3
import datetime
import csv
from botocore.exceptions import ClientError

# -------------------- CONFIG --------------------

# AWS CLI profile in your ~/.aws/config / ~/.aws/credentials
# prior to running this script, authenticate to CBP Payer manually
#   aws sso login --profile my-admin-profile
PROFILE = "my-admin-profile"

# Role that exists in each member account, trusted by the management account
ORG_ROLE_NAME = "OrgS3ReadRole"

# How far back to look in CloudWatch for a datapoint
DAYS_LOOKBACK = 3

# Output CSV file
CSV_FILENAME = "s3_inventory.csv"

# List of storage types we care about (can be adjusted)
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
org_client = base_session.client("organizations")
sts_client = base_session.client("sts")


def assume_role(account_id, role_name):
    """Assume the cross-account role in the given account and return a session."""
    role_arn = f"arn:aws:iam::{account_id}:role/{role_name}"
    resp = sts_client.assume_role(
        RoleArn=role_arn,
        RoleSessionName="OrgS3InventorySession"
    )
    creds = resp["Credentials"]
    return boto3.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
    )


def list_org_accounts():
    """Return a list of ACTIVE account IDs in the organization."""
    accounts = []
    token = None

    while True:
        if token:
            resp = org_client.list_accounts(NextToken=token)
        else:
            resp = org_client.list_accounts()

        for acc in resp["Accounts"]:
            if acc["Status"] == "ACTIVE":
                accounts.append(acc["Id"])

        token = resp.get("NextToken")
        if not token:
            break

    return accounts


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


def write_results_to_csv(results, filename):
    """Write the aggregated results to CSV."""
    # Collect all storage-type keys that appeared in any result
    all_storage_types = set()
    for r in results:
        all_storage_types.update(r["by_storage_type"].keys())
    storage_type_columns = sorted(all_storage_types)

    fieldnames = ["account_id", "bucket_name", "region", "total_bytes"] + storage_type_columns

    with open(filename, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for r in results:
            row = {
                "account_id": r["account_id"],
                "bucket_name": r["bucket_name"],
                "region": r["region"],
                "total_bytes": int(r["total_bytes"]),
            }
            for stype in storage_type_columns:
                row[stype] = int(r["by_storage_type"].get(stype, 0))
            writer.writerow(row)

    print(f"\nWrote {len(results)} rows to {filename}")


def main():
    accounts = list_org_accounts()
    print(f"Found {len(accounts)} active accounts in the organization")

    results = []

    for account_id in accounts:
        print(f"\n=== Account {account_id} ===")

        try:
            member_session = assume_role(account_id, ORG_ROLE_NAME)
        except ClientError as e:
            print(f"  [ERROR] Could not assume role in account {account_id}: {e}")
            continue

        s3 = member_session.client("s3")

        try:
            buckets_resp = s3.list_buckets()
        except ClientError as e:
            print(f"  [ERROR] list_buckets failed in account {account_id}: {e}")
            continue

        for b in buckets_resp.get("Buckets", []):
            bucket_name = b["Name"]
            region = get_bucket_region(s3, bucket_name)
            if not region:
                continue

            print(f"  Bucket: {bucket_name} (region: {region})")

            # CloudWatch client must be in the bucket's region
            cw = member_session.client("cloudwatch", region_name=region)

            sizes_by_type = get_bucket_size_bytes(cw, bucket_name)
            total_bytes = sum(sizes_by_type.values())

            print(f"    Total size (bytes): {int(total_bytes)}")

            results.append({
                "account_id": account_id,
                "bucket_name": bucket_name,
                "region": region,
                "total_bytes": total_bytes,
                "by_storage_type": sizes_by_type,
            })

    write_results_to_csv(results, CSV_FILENAME)


if __name__ == "__main__":
    main()
