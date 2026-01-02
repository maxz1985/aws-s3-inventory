import boto3
import datetime
import csv
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Set
from botocore.exceptions import ClientError

# -------------------- CONFIG --------------------

PROFILE = "my-account-profile"  # aws sso login --profile my-account-profile
DAYS_LOOKBACK = 3
CSV_FILENAME = "s3_inventory_single_account.csv"

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

# ---------------------------------------------------------
# Context + facts
# ---------------------------------------------------------

@dataclass
class BucketBase:
    bucket_name: str
    region: str

@dataclass
class RowContext:
    # stable base
    base: BucketBase
    # clients
    session: Any
    s3: Any
    # lazily-populated facts (filled by fact collectors)
    facts: Dict[str, Any] = field(default_factory=dict)

# ---------------------------------------------------------
# AWS helpers
# ---------------------------------------------------------

def get_bucket_region(s3_client, bucket_name: str) -> str | None:
    try:
        resp = s3_client.get_bucket_location(Bucket=bucket_name)
        loc = resp.get("LocationConstraint")
        return "us-east-1" if loc is None else loc
    except ClientError as e:
        print(f"  [WARN] get_bucket_location failed for {bucket_name}: {e}")
        return None

# -------------------- FACT COLLECTORS --------------------
# Fact collectors are only called if at least one ENABLED column needs that fact.

def fact_account_id(ctx: RowContext) -> str:
    sts = ctx.session.client("sts")
    return sts.get_caller_identity()["Account"]

def fact_tags(ctx: RowContext) -> Dict[str, str]:
    try:
        resp = ctx.s3.get_bucket_tagging(Bucket=ctx.base.bucket_name)
        return {t["Key"]: t["Value"] for t in resp.get("TagSet", [])}
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchTagSet":
            return {}
        print(f"  [WARN] get_bucket_tagging failed for {ctx.base.bucket_name}: {e}")
        return {}

def fact_sizes_by_type(ctx: RowContext) -> Dict[str, float]:
    cw = ctx.session.client("cloudwatch", region_name=ctx.base.region)
    end_time = datetime.datetime.utcnow()
    start_time = end_time - datetime.timedelta(days=DAYS_LOOKBACK)

    sizes: Dict[str, float] = {}
    for stype in STORAGE_TYPES:
        try:
            resp = cw.get_metric_statistics(
                Namespace="AWS/S3",
                MetricName="BucketSizeBytes",
                Dimensions=[
                    {"Name": "BucketName", "Value": ctx.base.bucket_name},
                    {"Name": "StorageType", "Value": stype},
                ],
                StartTime=start_time,
                EndTime=end_time,
                Period=86400,
                Statistics=["Average"],
            )
            dps = resp.get("Datapoints", [])
            if not dps:
                continue
            latest = max(dps, key=lambda d: d["Timestamp"])
            sizes[stype] = latest["Average"]
        except ClientError as e:
            print(f"    [WARN] CW metric error bucket={ctx.base.bucket_name} type={stype}: {e}")
    return sizes

FACT_COLLECTORS: Dict[str, Callable[[RowContext], Any]] = {
    "account_id": fact_account_id,
    "tags": fact_tags,
    "sizes_by_type": fact_sizes_by_type,
}

def ensure_facts(ctx: RowContext, needed: Set[str]) -> None:
    """Populate ctx.facts for the requested fact keys (only once)."""
    for key in needed:
        if key in ctx.facts:
            continue
        collector = FACT_COLLECTORS.get(key)
        if collector is None:
            raise KeyError(f"No fact collector registered for '{key}'")
        ctx.facts[key] = collector(ctx)

# -------------------- COLUMN PROVIDERS --------------------
# Column providers should be simple, using base + facts.

def col_bucket_name(ctx: RowContext) -> str:
    return ctx.base.bucket_name

def col_region(ctx: RowContext) -> str:
    return ctx.base.region

def col_account_id(ctx: RowContext) -> str:
    return ctx.facts.get("account_id", "")

def col_total_bytes(ctx: RowContext) -> int:
    sizes = ctx.facts.get("sizes_by_type", {})
    return int(sum(sizes.values()))

def col_tags_kv(ctx: RowContext) -> str:
    tags = ctx.facts.get("tags", {})
    if not tags:
        return ""
    return ";".join(f"{k}={v}" for k, v in sorted(tags.items()))

def _tag_value(ctx: RowContext, key: str) -> str:
    tags = ctx.facts.get("tags", {})
    return tags.get(key, "") or ""

def col_tag_cost_center(ctx: RowContext) -> str:
    return _tag_value(ctx, "cost_center")

def col_tag_environment(ctx: RowContext) -> str:
    return _tag_value(ctx, "environment")

def make_storage_type_provider(storage_type: str) -> Callable[[RowContext], int]:
    def _provider(ctx: RowContext) -> int:
        sizes = ctx.facts.get("sizes_by_type", {})
        return int(sizes.get(storage_type, 0))
    return _provider

# -------------------- COLUMN REGISTRY (EDIT HERE) --------------------
# This is the "single convenient place" you asked for.
# To enable/disable a column: flip enabled True/False (or comment the block out).
# No functionality is lost.

@dataclass(frozen=True)
class ColumnDef:
    name: str
    enabled: bool
    provider: Callable[[RowContext], Any]
    requires: Set[str] = frozenset()  # facts needed (keys in FACT_COLLECTORS)

COLUMNS: List[ColumnDef] = [
    ColumnDef("bucket_name", True,  col_bucket_name, requires=set()),
    ColumnDef("region",      True,  col_region,      requires=set()),

    # Toggle this on later without deleting any code:
    ColumnDef("account_id",  False, col_account_id,  requires={"account_id"}),

    ColumnDef("total_bytes", True,  col_total_bytes, requires={"sizes_by_type"}),
    ColumnDef("tags",        True,  col_tags_kv,     requires={"tags"}),

    # Derived from tags (no extra AWS calls beyond tags):
    ColumnDef("tag_cost_center",  True, col_tag_cost_center,  requires={"tags"}),
    ColumnDef("tag_environment",  True, col_tag_environment,  requires={"tags"}),

    # Uncomment these if you want per-storage-type columns:
    # *[ColumnDef(st, True, make_storage_type_provider(st), requires={"sizes_by_type"}) for st in STORAGE_TYPES],
]

def enabled_columns() -> List[ColumnDef]:
    return [c for c in COLUMNS if c.enabled]

def enabled_fieldnames(cols: List[ColumnDef]) -> List[str]:
    return [c.name for c in cols]

def required_facts(cols: List[ColumnDef]) -> Set[str]:
    req: Set[str] = set()
    for c in cols:
        req |= set(c.requires)
    return req

# ---------------------------------------------------------
# Main
# ---------------------------------------------------------

def main() -> None:
    session = boto3.Session(profile_name=PROFILE)
    s3 = session.client("s3")

    cols = enabled_columns()
    fieldnames = enabled_fieldnames(cols)
    facts_needed_globally = required_facts(cols)

    print(f"Enabled columns: {fieldnames}")
    print(f"Facts needed: {sorted(facts_needed_globally)}")

    try:
        buckets_resp = s3.list_buckets()
    except ClientError as e:
        print(f"[ERROR] list_buckets failed: {e}")
        return

    buckets = buckets_resp.get("Buckets", [])
    print(f"Found {len(buckets)} buckets")

    with open(CSV_FILENAME, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for b in buckets:
            bucket_name = b["Name"]
            region = get_bucket_region(s3, bucket_name)
            if not region:
                continue

            print(f"Bucket: {bucket_name} ({region})")

            ctx = RowContext(
                base=BucketBase(bucket_name=bucket_name, region=region),
                session=session,
                s3=s3,
            )

            # Only collect the facts needed by enabled columns
            ensure_facts(ctx, facts_needed_globally)

            row: Dict[str, Any] = {}
            for c in cols:
                row[c.name] = c.provider(ctx)

            writer.writerow(row)

    print(f"\nWrote CSV: {CSV_FILENAME}")

if __name__ == "__main__":
    main()
