# **Configuring `NO_PROXY` for AWS EC2 Instances**

This document provides a **universal, drop-in `NO_PROXY` configuration** for Amazon EC2 instances.
The configuration is designed to support **all AWS agents and services**, avoid metadata failures, and prevent common SSM connectivity issues.

---

## ## **1. Purpose**

AWS EC2 instances frequently require outbound access through a corporate proxy.
However, several AWS components **must bypass the proxy** to function correctly:

* AWS Instance Metadata Service (IMDSv2)
* AWS Systems Manager (SSM) Agent
* CloudWatch Agent
* ECS and EKS node agents
* Applications that use instance-profile credentials via IMDS
* Any AWS service accessed through VPC Interface Endpoints (PrivateLink)

To ensure correct operation, specific IPs, hostnames, and metadata URLs **must be excluded** from proxy use.

This document defines a **universal `NO_PROXY` string** that works reliably across all current AWS EC2 scenarios.

---

## ## **2. Universal `NO_PROXY` Configuration**

Use the following configuration in `/etc/environment`, shell profiles, or systemd service overrides.

### ### **Recommended Universal Value**

```bash
NO_PROXY=169.254.169.254,169.254.170.2,169.254.169.123,localhost,127.0.0.1,::1,metadata.amazonaws.com,169.254.169.254/latest,169.254.170.2/v2/credentials
no_proxy=169.254.169.254,169.254.170.2,169.254.169.123,localhost,127.0.0.1,::1,metadata.amazonaws.com,169.254.169.254/latest,169.254.170.2/v2/credentials
```

This string is safe to use across:

* Standard EC2 instances
* SSM-managed instances
* ECS container instances
* EKS worker nodes
* IMDSv2-required environments
* EC2 instances using VPC Interface Endpoints (PrivateLink)

---

## ## **3. Explanation of `NO_PROXY` Entries**

| Entry                           | Purpose                                                                                                                                             |
| ------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `169.254.169.254`               | Primary AWS Instance Metadata Service (IMDS) endpoint (v1 & v2 token). Required by SSM, CW Agent, ECS/EKS, and anything using instance credentials. |
| `169.254.170.2`                 | ECS Task Metadata / Credential endpoint. Also used by Bottlerocket and some SDK paths.                                                              |
| `169.254.169.123`               | Additional link-local IP occasionally used by AWS agents. Safe universal inclusion.                                                                 |
| `localhost`, `127.0.0.1`, `::1` | Prevents proxying traffic destined for loopback/local services.                                                                                     |
| `metadata.amazonaws.com`        | Hostname alias used by some AWS SDKs and environments for IMDS.                                                                                     |
| `169.254.169.254/latest`        | Covers clients that incorrectly evaluate NO_PROXY against full URLs.                                                                                |
| `169.254.170.2/v2/credentials`  | Same as above for ECS Task credentials path.                                                                                                        |

Including these entries ensures AWS agents never attempt to use a proxy for metadata or credential retrieval.

---

## ## **4. Applying the Configuration System-Wide**

### ### `/etc/environment`

Add (or update):

```bash
HTTP_PROXY=http://yourproxy:port
HTTPS_PROXY=http://yourproxy:port
NO_PROXY=169.254.169.254,169.254.170.2,169.254.169.123,localhost,127.0.0.1,::1,metadata.amazonaws.com,169.254.169.254/latest,169.254.170.2/v2/credentials
no_proxy=169.254.169.254,169.254.170.2,169.254.169.123,localhost,127.0.0.1,::1,metadata.amazonaws.com,169.254.169.254/latest,169.254.170.2/v2/credentials
```

Log out and log back in to apply.

---

## ## **5. Ensuring SSM Agent Inherits the `NO_PROXY` Settings**

### ### Edit the SSM Agent systemd service:

```bash
sudo systemctl edit amazon-ssm-agent
```

Insert:

```ini
[Service]
Environment="NO_PROXY=169.254.169.254,169.254.170.2,169.254.169.123,localhost,127.0.0.1,::1,metadata.amazonaws.com,169.254.169.254/latest,169.254.170.2/v2/credentials"
Environment="no_proxy=169.254.169.254,169.254.170.2,169.254.169.123,localhost,127.0.0.1,::1,metadata.amazonaws.com,169.254.169.254/latest,169.254.170.2/v2/credentials"
```

Then reload and restart:

```bash
sudo systemctl daemon-reload
sudo systemctl restart amazon-ssm-agent
```

This step is essential—SSM Agent does **not** automatically inherit environment settings.

---

## ## **6. Validation**

To verify that IMDS is reachable directly (not via proxy):

```bash
curl -v --noproxy '*' http://169.254.169.254/latest/meta-data/instance-id
```

If this returns the instance ID successfully, IMDS is reachable and the proxy configuration is correct.

To verify SSM Agent identity retrieval:

```bash
sudo tail -f /var/log/amazon/ssm/amazon-ssm-agent.log
```

Look for **absence** of:

```
failed to assume any identity
failed to fetch IMDS token
```

---

## ## **7. Summary**

* AWS EC2 instances behind proxies must bypass IMDS and certain local endpoints.
* The universal `NO_PROXY` string prevents outages in SSM, ECS, EKS, CW Agent, and AWS SDKs.
* This document’s configuration is safe to use across all typical AWS EC2 environments.
* Use this as the **default standard** whenever proxies are configured on EC2 systems.

---

If you want, I can also generate:

* A shorter “minimal required” version
* A “PrivateLink-optimized” version
* A Windows version for EC2 Windows instances
* A separate page explaining *why* SSM fails without NO_PROXY
