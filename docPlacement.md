# Documentation Placement

## 1. Who/what goes where (code vs README vs Sphinx)

An intersection of **audience** and **scope**:

* **Code comments/docstrings** → for people *reading or maintaining the code*
* **README in repo** → for people *using or developing this repo*
* **Sphinx docs** → for people *using the system/procedure in the organization*, possibly without ever opening the repo

Here’s a quick comparison:

| Place                          | Primary audience           | Scope                              | Good for                                                               | Avoid putting                                                         |
|--------------------------------|----------------------------|------------------------------------|------------------------------------------------------------------------|-----------------------------------------------------------------------|
| **Code comments / docstrings** | Implementers & maintainers | Function/module-level              | Why something is done, non-obvious logic, constraints, public API docs | Step-by-step run procedures, environment-specific how-tos, org policy |
| **Repo README.md**             | Users & devs of this repo  | Repo/package-level                 | What this project is, how to run/develop/test, config, examples        | Org-wide runbooks, cross-repo processes, long-form tutorials          |
| **Sphinx (org docs)**          | Org users & operators      | System/procedure-level, cross-repo | Runbooks, SOPs, onboarding, “how this fits in our environment”         | Very low-level implementation notes, code minutiae                    |

Rule of thumb:

* **Code comments**: *How & why the code works*
* **README**: *How to use and work on this repo*
* **Sphinx**: *How this is used in our environment / process*

---

## 2. Example -  S3 inventory script

### A. Code comments & docstrings (inside the script / package)

Goal: future you (or another dev) can maintain this without guessing.

**Put here:**

* **Function/module docstrings**:

  * What each function does, key parameters, return values.
  * Any assumptions: e.g. “Assumes caller has `s3:ListAllMyBuckets` and `s3:GetBucketMetricsConfiguration`”.
* **Non-obvious logic and gotchas**:

  * Why you chose one AWS API over another.
  * Performance constraints (“We batch calls to avoid X API limit”, “We stream results to reduce memory use”).
  * Edge cases: “We skip buckets in regions where CloudWatch metrics are disabled.”
* **Structured CLI help**:

  * If it’s a CLI tool, its `--help` text and argument descriptions belong here (argparse / click help strings). That becomes the single source of truth for flags/options.

**Examples:**

```python
def list_buckets_with_usage(org_accounts: list[str]) -> list[BucketUsage]:
    """
    Enumerate all S3 buckets across the given AWS accounts and
    return their storage usage in bytes.

    - Assumes federated role `OrgBucketInventoryRole` exists in each account.
    - Uses CloudWatch metrics (BucketSizeBytes) at the 'StorageType=StandardStorage' level.
    - Only supports regions listed in SUPPORTED_REGIONS.
    """
```

Inline comment:

```python
# We use 'ListAccounts' from AWS Organizations rather than a static list
# so that newly-added accounts are picked up automatically.
```

**Don’t put here:**

* “How do I schedule this as a weekly job via Jenkins/CircleCI?”
* “Which team owns this and where do I open a ticket?”
  Those belong higher up.

---

### B. Repo README.md (top-level of the script’s repo)

Goal: someone clones the repo and can:

* Understand what this project is
* Run it locally / in the pipeline
* Develop on it
* Find the org-level procedure

**Put here:**

1. **Short overview**

   * 2–3 sentences: “This tool inventories all S3 buckets across our AWS Organization and exports per-bucket storage usage to CSV/…”.

2. **Quickstart / Usage**

   * How to run it locally from the command line.
   * Examples:

     ```bash
     # Basic usage
     python -m s3_inventory --output s3_inventory.csv

     # Specify profile / region override
     python -m s3_inventory --profile org-audit --region us-east-1 --output s3_inventory.csv
     ```

3. **Installation / Dependencies**

   * Python version, required libraries.
   * How to install: `pip install -r requirements.txt` or `poetry install`.

4. **Configuration**

   * How it picks up AWS credentials (profile, SSO, env vars).
   * IAM requirements (roles/policies needed).
   * Any config file layout.

5. **Development / Contribution**

   * How to run tests: `pytest`, `make test`.
   * Linting/formatting: `ruff`, `black`, etc.
   * How to run pre-commit hooks / CI locally if relevant.

6. **Link to the Sphinx procedure**

   * Very important to avoid duplication:

     > For the operational runbook used in production, see: [S3 Inventory Runbook](link to Sphinx page).

**Optional:**

* Brief architecture note or diagram (at most one section). If it gets big, move detailed architecture to Sphinx and just link it.

**Avoid in README:**

* Full, step-by-step SOP for “Monthly Org S3 Compliance Check” that multiple teams follow.
  That belongs in Sphinx as an org-facing runbook, with the README pointing to it.

---

### C. Sphinx docs (org-wide documentation)

Goal: someone somewhere in the org hears “Run the S3 inventory” and finds:

* What that means.
* Who runs it.
* How often.
* The exact steps in *their* environment.

**Put here:**

1. **Conceptual description / context**

   * “The S3 Inventory Tool is used to get per-bucket storage usage across our AWS Organization for cost/compliance reporting.”

2. **Runbook / Procedure**

   * Clearly numbered steps like:

     1. Log in to the `OrgAudit` AWS account using SSO profile `org-audit-admin`.
     2. Navigate to Jenkins job `s3-inventory-prod` and click “Build with Parameters”.
     3. For ad-hoc CLI runs, follow the instructions in [Repo README] and ensure your AWS profile is set to `org-audit-readonly`.
     4. Output is saved in `s3://central-reports/s3-inventory/YYYY/MM/DD/`.
   * Preconditions: what roles/permissions they need.
   * Frequency: monthly/weekly/on-demand.
   * Ownership: “Owned by Cloud Platform Team. Contact: #cloud-platform Slack channel.”

3. **Environment-specific details**

   * Where the script is deployed (EC2, Lambda, container).
   * Any integration: “Job X in GitLab CI triggers this script,” “Results feed into Power BI dashboard Y.”

4. **Troubleshooting**

   * Common errors (e.g., missing IAM permissions, Organizations API throttling).
   * Where to check logs.
   * Escalation path.

5. **Links**

   * Link back to the Git repo and README.
   * Possibly auto-generated API docs from your Python docstrings if you use `sphinx.ext.autodoc`.

**Avoid in Sphinx:**

* Very low-level implementation commentary (“We had to work around a boto3 bug in version 1.20”). That belongs in code/README issues.

---

## 3. Simple decision rules

When you’re unsure:

* **Is this about *how the code works* or justifying implementation decisions?**
  → Code comments/docstrings.

* **Is this about *how to use or develop this project once you’ve cloned it*?**
  → README (plus maybe `/docs` inside repo that Sphinx pulls in).

* **Is this about *how someone in the org executes a procedure, possibly without touching the repo*?**
  → Sphinx runbook.

* **Is it org-wide, cross-repo, or policy-like (e.g., “all inventory tools must write reports to XYZ bucket”)?**
  → Sphinx, with links down to specific repos.

* **Is it duplicated in multiple places?**

  * Pick **one source of truth** (usually code for options/flags, or Sphinx for procedures)
  * Make other places *link* to it.

---