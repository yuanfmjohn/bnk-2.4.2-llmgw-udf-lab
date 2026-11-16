# F5 BIG-IP Next for Kubernetes (BNK) v2.4.1 - AI Gateway Deployment & Operations Guide

This guide details the deployment, architecture, and operational verification of the **AI Gateway** feature on **F5 BIG-IP Next for Kubernetes (BNK) v2.4.1**, aligned with the official reference architecture from [mwiget/ocibnkctl - LLM Gateway How-To (Path 3: your own BNK cluster, without ocibnkctl)](https://github.com/mwiget/ocibnkctl/blob/main/docs/llm-gateway-howto.md#path-3-your-own-bnk-cluster-without-ocibnkctl).

---

## 1. Overview & Architecture

BNK AI Gateway integrates high-performance ingress routing, LLM protocol intelligence, intelligent prompt caching, token rate limiting, and inference observability into the BIG-IP Next for Kubernetes data plane.

```
+-----------------------------------------------------------------------------------+
| Kubernetes Cluster (BNK Namespace: f5-bnk-instance)                              |
|                                                                                   |
|  [ Client / Application ]                                                         |
|             │                                                                     |
|             ▼ (HTTP / OpenAI API Request)                                         |
|  ┌────────────────────────────────────────────────────────┐                       |
|  │ F5 TMM Data Plane (f5-tmm on worker1, worker3)         │                       |
|  │  ├─ HTTP Ingress / Gateway API Listener                │                       |
|  │  ├─ F5BigDnsCache (aigw-dns-resolver -> kube-dns)      │                       |
|  │  ├─ Prompt Caching & Token Rate-Limiting               │                       |
|  │  └─ Stream Telemetry & Token Accounting                │                       |
|  └────────────────────┬──────────────────┬────────────────┘                       |
|                       │                  │                                        |
|         (Forward)     │                  │ (Async Otel Logs & Metrics)            |
|                       ▼                  ▼                                        |
|             ┌─────────────────┐   ┌─────────────────────────────────────────┐     |
|             │ Backend LLM /   │   │ AI Gateway Platform Subsystem           │     |
|             │ Ollama / OpenAI │   │  ├─ f5-aigateway-controller             │     |
|             └─────────────────┘   │  ├─ ClickHouse (Metrics / Audit Log)    │     |
|                                   │  │   └─ PersistentVolumeClaim (NFS)     │     |
|                                   │  └─ f5-aigateway-console (UI / Dashboard)│    |
|                                   └─────────────────────────────────────────┘     |
+-----------------------------------------------------------------------------------+
```

### Key Components

| Component | Pod / Deployment | Role |
| :--- | :--- | :--- |
| **CNE / AI Gateway Controller** | `f5-cne-controller` | Translates Gateway API & AI Gateway CRDs (`AIProvider`, `AIModel`, `AIGatewayProfile`, `AppPolicy`) into TMM configurations and exposes the governance REST API on port `8443` (`f5-cne-controller-api`). |
| **Telemetry & Metrics Store** | `f5-aigateway-clickhouse-0` | High-throughput columnar database for storing LLM prompt latency, token metrics, and operational traces (`otel.aigw_requests`). |
| **Management Console** | `f5-aigateway-console` | Web UI (NodePort `30964`) for viewing model metrics, usage dashboards, governance entities, and token latency statistics. |
| **GatewayClass** | `bnk-gatewayclass` | Gateway API resource binding client traffic to the BNK TMM controller (`f5.com/<ns>-f5-cne-controller`). |
| **DNS Cache** | `aigw-dns-resolver` (`F5BigDnsCache`) | Resolves external LLM provider endpoints (e.g. Google Gemini, NVIDIA NGC, OpenAI) via CoreDNS (`10.96.0.10:53`). |

---

## 2. Prerequisites

Before enabling the AI Gateway feature in BNK:

1. **Active BNK v2.4 Deployment**: BNK must be fully deployed using F5 Lifecycle Orchestrator (FLO) v2.30.0 with licensed TMM instances (see [BNK Installation & Licensing Guide](bnk-installation-licensing-guide.md)).
2. **Kubernetes Gateway API CRDs**: Gateway API v1.x CRDs must be present on the cluster:
   ```bash
   kubectl get crd gatewayclasses.gateway.networking.k8s.io gateways.gateway.networking.k8s.io httproutes.gateway.networking.k8s.io
   ```
3. **StorageClass with ReadWriteOnce and POSIX Permissions**: ClickHouse requires a `ReadWriteOnce` persistent volume.
   > [!IMPORTANT]
   > When utilizing an NFS server backing the CSI driver (e.g., `client` node at `10.1.1.4:/mnt/nfs_share`), the NFS export **must** be exported with `no_root_squash` in `/etc/exports`:
   > ```text
   > /mnt/nfs_share 10.1.1.0/24(rw,sync,no_subtree_check,no_root_squash)
   > ```
   > If `all_squash` or standard squash is active, the ClickHouse initialization container will fail with:
   > `chown: changing ownership of '/var/lib/clickhouse/': Operation not permitted`.

---

## 3. Deployment Procedure (Step-by-Step)

### Step 1: Verify Existing CNEInstance Configuration

Check the existing CNEInstance in the BNK namespace:

```bash
kubectl get cneinstance -n f5-bnk-instance -o yaml
```

Confirm that the logging subsystem, DSSM Sentinel (Redis), and TMM instances are healthy.

### Step 2: Enable AI Gateway on CNEInstance

Patch the `f5-bnk-instance` resource to enable `aiGateway` and `aiMetrics`:

```bash
kubectl patch cneinstance f5-bnk-instance -n f5-bnk-instance --type=merge -p '{
  "spec": {
    "cne": {
      "aiGateway": {
        "enabled": true,
        "aiMetrics": true
      }
    }
  }
}'
```

Alternatively, apply via declarative YAML:

```yaml
apiVersion: k8s.f5net.com/v1
kind: CNEInstance
metadata:
  name: f5-bnk-instance
  namespace: f5-bnk-instance
spec:
  cne:
    aiGateway:
      enabled: true
      aiMetrics: true
    # Existing TMM, DSSM, and license configurations preserved...
```

### Step 3: Monitor FLO Reconciliation

Upon patching, the F5 Lifecycle Orchestrator (`f5-cne-orchestration-controller`) creates child resources for the AI Gateway subsystem:

```bash
kubectl get pods -n f5-bnk-instance -w
```

You will observe:
1. `f5-aigateway-clickhouse-0`: Deploys the ClickHouse stateful pod and mounts the `f5-aigateway-clickhouse-pvc`.
2. `f5-aigateway-console-*`: Deploys the management web console (exposed via NodePort `30964`).
3. `f5-cne-controller-*`: Reconciles AI Gateway CRDs and serves the internal REST API (`f5-cne-controller-api:8443`).
4. `F5BigDnsCache/aigw-dns-resolver`: Created automatically to forward external LLM DNS queries to CoreDNS.

Verify pod status:
```bash
kubectl get pods -n f5-bnk-instance | grep -E "aigateway|cne-controller|tmm|dssm"
```

*Expected Output:*
```text
f5-aigateway-clickhouse-0               1/1     Running   0          ...
f5-aigateway-console-7d84cf9dcc-h2zh9   1/1     Running   0          ...
f5-cne-controller-bccd75cd6-vlxss       3/3     Running   0          ...
f5-dssm-db-0                            3/3     Running   0          ...
f5-dssm-db-1                            3/3     Running   0          ...
f5-dssm-db-2                            3/3     Running   0          ...
f5-tmm-8r9qx                            7/7     Running   0          ...
f5-tmm-lmnp9                            7/7     Running   0          ...
```

---

## 4. GatewayClass and CoreDNS Integration

To allow standard Kubernetes Gateway API `Gateway` and `HTTPRoute` resources to bind to BNK:

### 1. Create the BNK GatewayClass

Create the GatewayClass pointing to the CNE controller:

```bash
cat << 'EOF' | kubectl apply -f -
apiVersion: gateway.networking.k8s.io/v1
kind: GatewayClass
metadata:
  name: bnk-gatewayclass
spec:
  controllerName: f5.com/f5-bnk-instance-f5-cne-controller
EOF
```

> [!NOTE]
> The `controllerName` format is `f5.com/<namespace>-f5-cne-controller`. Because BNK is deployed in namespace `f5-bnk-instance`, the value must be `f5.com/f5-bnk-instance-f5-cne-controller`.

Verify the GatewayClass status:
```bash
kubectl get gatewayclass bnk-gatewayclass
```
*Expected Output:*
```text
NAME               CONTROLLER                                     ACCEPTED   AGE
bnk-gatewayclass   f5.com/f5-bnk-instance-f5-cne-controller      True       15m
```

### 2. Verify Automated DNS Caching (`F5BigDnsCache`)

BNK requires TMM to resolve external LLM backends (such as `api.openai.com` or cluster-internal backend services). When AI Gateway is enabled, FLO automatically creates an `F5BigDnsCache` custom resource targeting the cluster CoreDNS service (`10.96.0.10:53`):

```bash
kubectl get f5bigdnscache -n f5-bnk-instance
```

Inspect the DNS cache configuration:
```bash
kubectl get f5bigdnscache aigw-dns-resolver -n f5-bnk-instance -o yaml
```

*Key Spec Elements:*
```yaml
spec:
  dnsCache:
    forwarders:
    - 10.96.0.10:53
    name: aigw-dns-resolver
```

---

## 5. Console UI Access & Credentials

The AI Gateway provides a web management console (`f5-aigateway-console`) for monitoring LLM queries, latency distributions, token consumption metrics, and tenant governance.

### Console Service & Access Methods

BNK exposes `f5-aigateway-console` as a `NodePort` service on port `30964` (mapping to container port `3000`):

```bash
kubectl get svc f5-aigateway-console -n f5-bnk-instance -o wide
```

*Access Options:*
- **UDF Portal Shortcut:** Use the **`llmgw-console`** access method on **`worker4`** in the UDF lab UI.
- **Direct NodePort URL:** `http://<node-ip>:30964` (for example, `http://10.1.1.9:30964` on `worker4` or any cluster node IP).
- **SSH Local Port Forward (if NodePort is not directly reachable):**
  ```bash
  ssh -L 3000:10.1.1.9:30964 ubuntu@<controller-or-jump-host>
  # Open http://localhost:3000
  ```

*(If deploying without the built-in NodePort service, create a custom `NodePort` service targeting `app.kubernetes.io/name: f5-aigateway-console` on port `3000` -> `nodePort: 30964`.)*

### Retrieve System & Tenant Administrator Credentials

The platform stores system-wide and per-tenant admin credentials in Kubernetes Secrets within namespace `f5-bnk-instance`:

```bash
# 1. System / Platform Admin (f5-aigateway-admin)
kubectl get secret f5-aigateway-admin -n f5-bnk-instance -o jsonpath='{.data.username}' | base64 --decode; echo ""
kubectl get secret f5-aigateway-admin -n f5-bnk-instance -o jsonpath='{.data.password}' | base64 --decode; echo ""

# 2. Tenant Admin (aitenant-<tenant>-admin, e.g., demo / llmgw-demo)
kubectl get secret aitenant-demo-admin -n f5-bnk-instance -o jsonpath='{.data.username}' | base64 --decode; echo ""
kubectl get secret aitenant-demo-admin -n f5-bnk-instance -o jsonpath='{.data.password}' | base64 --decode; echo ""

# List all AI Gateway admin secrets
kubectl get secrets -n f5-bnk-instance | grep -E "f5-aigateway-admin|aitenant-.*-admin"
```

| Scope / Role | Kubernetes Secret (`f5-bnk-instance`) | Username | Password |
| :--- | :--- | :--- | :--- |
| **System / Platform Admin** | `f5-aigateway-admin` | `admin` | `********` *(retrieve via `kubectl get secret` above)* |
| **Tenant `demo` Admin** | `aitenant-demo-admin` | `admin` | `********` *(retrieve via `kubectl get secret` above)* |
| **Tenant `llmgw-demo` Admin** | `aitenant-llmgw-demo-admin` | `admin` | `********` *(retrieve via `kubectl get secret` above)* |

---

## 6. Verification and Health Checks

### Verification Checklist

Execute these checks to ensure complete readiness:

```bash
# 1. Check all BNK & AI Gateway pods in f5-bnk-instance
kubectl get pods -n f5-bnk-instance -o wide

# 2. Verify CNEInstance status (Available=True, DSSMAvailable=True, AIGatewayAvailable=True)
kubectl get cneinstance f5-bnk-instance -n f5-bnk-instance -o jsonpath="{range .status.conditions[*]}{.type}={.status} ({.reason}){'\n'}{end}"

# 3. Check ClickHouse databases and OpenTelemetry table
kubectl exec f5-aigateway-clickhouse-0 -n f5-bnk-instance -- clickhouse-client --query "SHOW DATABASES; SHOW TABLES FROM otel;"

# 4. Verify GatewayClass status
kubectl get gatewayclass bnk-gatewayclass -o yaml | grep -A 5 status:
```

### ClickHouse Database Verification Output
```text
INFORMATION_SCHEMA
default
information_schema
otel
system
```

The `otel.aigw_requests` table holds the operational logs, prompt metrics, and token accounting records populated by TMM during LLM request handling.

---

## 7. Troubleshooting & Common Issues

### Issue 1: ClickHouse Container CrashLoopBackOff (`chown` permission denied)
- **Symptom:** `f5-aigateway-clickhouse-0` fails to start. Logs show `chown: changing ownership of '/var/lib/clickhouse/': Operation not permitted`.
- **Cause:** NFS storage export defaults to `root_squash`, preventing the ClickHouse init container from setting permissions for uid `101`.
- **Remedy:** On the NFS server (`client` node), update `/etc/exports` with `no_root_squash`, run `sudo exportfs -rav`, and restart the pod.

### Issue 2: Gateway API HTTPRoute Not Programming in TMM
- **Symptom:** An `HTTPRoute` created by the user is ignored or not accepted.
- **Cause:** Incorrect `gatewayClassName` specified in the parent `Gateway` resource, or mismatched controller name.
- **Remedy:** Ensure the `Gateway` references `gatewayClassName: bnk-gatewayclass`, and verify that `bnk-gatewayclass` specifies `spec.controllerName: f5.com/f5-bnk-instance-f5-cne-controller`.

### Issue 3: External LLM Backends Failing DNS Lookup
- **Symptom:** Client requests to OpenAI or external endpoints fail with 503 or resolution timeout.
- **Cause:** Missing or misconfigured `F5BigDnsCache` resource.
- **Remedy:** Check `kubectl get f5-big-dns-caches.k8s.f5net.com -n f5-bnk-instance`. Ensure the IP matches the CoreDNS ClusterIP (`kubectl get svc -n kube-system kube-dns`).

### Issue 4: TMM Data-Plane VLAN Unreachable (`ARP INCOMPLETE`)
- **Symptom:** Requests to VIP `10.1.200.85` fail or `f5-tmm` cannot ping `10.1.200.1` / `10.1.210.70`.
- **Cause:** Host secondary NICs (`ens6`, `ens7`, `ens8`) on `worker1` / `worker3` are `DOWN` due to malformed `/etc/netplan/99-extra-nics.yaml`. Multus `macvlan` (`passthru`) interfaces cannot transmit if the parent host NIC is down.
- **Remedy:** Fix `/etc/netplan/99-extra-nics.yaml` indentation, run `sudo netplan apply`, and bring the interfaces up (`sudo ip link set ens6 up && sudo ip link set ens7 up && sudo ip link set ens8 up`) on `worker1` and `worker3`.

### Issue 5: Controller REST API Returns HTTP 500 (`DSSMAvailable=False`)
- **Symptom:** `GET /api/v1/tenants` returns `HTTP 500`, and `f5-dssm-db-*` pods are `2/3 Running` with `SSL_connect failed: certificate verify failed`.
- **Cause:** Short-lived (1-hour) cert-manager TLS certificates rotated on disk in `/tls/dssm/mds/svr/`, but the running `redis-server` process still serves the expired certificate.
- **Remedy:** Delete the unready `f5-dssm-db-*` pod(s) so they restart and load the renewed certificate:
  ```bash
  kubectl delete pod f5-dssm-db-0 f5-dssm-db-2 -n f5-bnk-instance
  kubectl wait --for=condition=Ready pod -l app=f5-dssm-db -n f5-bnk-instance --timeout=120s
  ```

### Issue 6: Governance Org Creation Returns HTTP 422 (`was deleted; restore or purge it first`)
- **Symptom:** Creating `demo-org` via `POST /api/v1/tenants/demo/governance/orgs` fails with HTTP 422.
- **Cause:** A previously deleted organization remains as a tombstone in DSSM.
- **Remedy:** Restore via `POST /api/v1/tenants/demo/governance-tombstones/orgs/<org-id>/restore` or purge via `DELETE /api/v1/tenants/demo/governance-tombstones/orgs/<org-id>` (automated in `aigateway/governance/setup_keys.py`).

---

## 9. End-to-End LLM Gateway Deployment (Path 3)

This section details the end-to-end setup following the `ocibnkctl` LLM Gateway reference architecture, integrating a real upstream LLM provider (**Google AI Studio Gemini**) with tenant isolation, governance, and customized gateway policies.

### 9.1 Tenant & Provider Configuration

#### 1. Tenant Definition
```yaml
apiVersion: inference.k8s.f5.com/v1alpha1
kind: AITenant
metadata:
  name: demo
spec:
  displayName: Demo Tenant
```

#### 2. Provider Credential & Secret
```bash
kubectl create secret generic pk-demo-gemini -n f5-bnk-instance \
  --from-literal=api-key="<GOOGLE_AI_STUDIO_API_KEY>"
```

```yaml
apiVersion: inference.k8s.f5.com/v1alpha1
kind: AICredential
metadata:
  name: pk-demo-gemini
  namespace: f5-bnk-instance
spec:
  tenantRef:
    name: demo
  secretRef:
    name: pk-demo-gemini
    key: api-key
---
apiVersion: inference.k8s.f5.com/v1alpha1
kind: AIProvider
metadata:
  name: pk-demo-gemini
  namespace: f5-bnk-instance
spec:
  tenantRef:
    name: demo
  vendor: Gemini
  gemini: {}
  credentialRef:
    name: pk-demo-gemini
  enabled: true
```

#### 3. AI Models
```yaml
apiVersion: inference.k8s.f5.com/v1alpha1
kind: AIModel
metadata:
  name: demo-gemini-flash
  namespace: f5-bnk-instance
spec:
  tenantRef:
    name: demo
  name: gemini-flash-latest
  displayName: Gemini Flash Latest
  deployments:
  - name: google-gemini
    providerRef:
      name: pk-demo-gemini
    upstream: gemini-3.5-flash-lite
    dialects: [gemini]
    weight: 1
    state: Active
    pricing:
      inputPerMtok: 150000
      outputPerMtok: 600000
```

### 9.2 Route & Policy Configuration

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: demo-llm
  namespace: f5-bnk-instance
spec:
  gatewayClassName: bnk-gatewayclass
  addresses:
  - type: IPAddress
    value: 10.1.200.85
  listeners:
  - name: http
    protocol: HTTP
    port: 80
    allowedRoutes:
      kinds:
      - kind: HTTPRoute
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: demo-llm
  namespace: f5-bnk-instance
spec:
  parentRefs:
  - name: demo-llm
    sectionName: http
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /v1
    timeouts:
      request: 300s
      backendRequest: 300s
---
apiVersion: gateway.k8s.f5.com/v1alpha1
kind: AppPolicy
metadata:
  name: demo-llm
  namespace: f5-bnk-instance
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: Gateway
    name: demo-llm
    sectionName: http
  extensionRefs:
  - group: inference.k8s.f5.com
    kind: AIGatewayProfile
    name: demo-llm
```

### 9.3 Customized Gateway Profile (Prompt Caching & Failover)

```yaml
apiVersion: inference.k8s.f5.com/v1alpha1
kind: AIGatewayProfile
metadata:
  name: demo-llm
  namespace: f5-bnk-instance
spec:
  tenantRef:
    name: demo
  auth:
    virtualKeys: {}
  providers:
  - providerRef:
      name: pk-demo-gemini
  promptCache: {}
  failover:
    on429: true
    on5xx: true
    maxAttempts: 2
    cooldown: 30s
    honorRetryAfter: true
```

### 9.4 Governance Objects (Controller REST API)

Virtual keys and governance boundaries are configured via the internal controller API (`http://<f5-cne-controller-api-pod-ip>:8443`) using `aigateway/governance/setup_keys.py`:

1. **Organization:** `demo-org` (`POST /api/v1/tenants/demo/governance/orgs`, or restored via `POST /api/v1/tenants/demo/governance-tombstones/orgs/demo-org/restore` if previously soft-deleted)
2. **Team:** Default team created under `demo-org` (e.g. `t-ec30b88defa0d17c`, `GET/POST /api/v1/tenants/demo/governance/teams`)
3. **Principal:** `demo-app` (`POST /api/v1/tenants/demo/governance/principals`, `kind: "service"`)
4. **Virtual Keys:** Issued via `POST /api/v1/tenants/demo/virtual-keys` with `principalId: "demo-app"` and scoped `modelScope` (saved to `aigateway/tests/demo_vkey.txt`).

### 9.5 CoreDNS Dual-Stack (IPv6 Blackhole) Remediation

In IPv4-only edge transit topologies, TMM resolves upstream public FQDNs (`generativelanguage.googleapis.com`) to dual-stack A/AAAA addresses. To prevent TMM pool timeouts over unrouted IPv6 paths, configure CoreDNS to drop AAAA queries:

```yaml
# CoreDNS ConfigMap in kube-system:
Corefile: |
  .:53 {
      errors
      health
      ready
      template IN AAAA . {
          rcode NOERROR
      }
      kubernetes cluster.local in-addr.arpa ip6.arpa {
          pods insecure
          fallthrough in-addr.arpa ip6.arpa
          ttl 30
      }
      forward . /etc/resolv.conf
      cache 30
      loop
      reload
      loadbalance
  }
```

And configure `useIpv6: false` in `aigw-dns-resolver` and `security-fqdn-dns-resolver`.

### 9.6 Alternative Provider Option: NVIDIA NGC (OpenAI-Compatible)

To integrate an OpenAI-compatible upstream such as NVIDIA NGC (`https://integrate.api.nvidia.com/v1`) instead of Google Gemini, use `aigateway/02-secret-pk-demo-ngc.yaml` through `aigateway/06-ai-gateway-profile.yaml` (`vendor: OpenAI`, `openAI.baseURL: https://integrate.api.nvidia.com/v1`, models `deepseek-ai/deepseek-v4.1-flash` and `openai/gpt-oss-20b`).

---

## 10. Operational Validation & Test Results

Run `/home/ubuntu/aigateway/tests/run_test.sh 10.1.200.85` from the `client` host (`10.1.10.4`).

### 10.1 Basic Gateway Verification

**1. Model Discovery (`GET /v1/models`):**
```bash
VKEY=$(cat /home/ubuntu/aigateway/tests/demo_vkey.txt | tr -d '\r\n')
curl -s http://10.1.200.85/v1/models \
  -H "Authorization: Bearer ${VKEY}"
```
```json
{
  "object": "list",
  "data": [
    {
      "id": "gemini-3.5-flash-lite",
      "object": "model",
      "created": 0,
      "owned_by": "bnk-ai-gateway",
      "type": "model",
      "display_name": "gemini-3.5-flash-lite",
      "created_at": "1970-01-01T00:00:00Z"
    },
    {
      "id": "gemini-flash-latest",
      "object": "model",
      "created": 0,
      "owned_by": "bnk-ai-gateway",
      "type": "model",
      "display_name": "gemini-flash-latest",
      "created_at": "1970-01-01T00:00:00Z"
    }
  ],
  "has_more": false,
  "first_id": "gemini-3.5-flash-lite",
  "last_id": "gemini-flash-latest"
}
```

**2. Chat Completion (`POST /v1/chat/completions`):**
```bash
curl -s -i http://10.1.200.85/v1/chat/completions \
  -H "Authorization: Bearer ${VKEY}" \
  -H "Content-Type: application/json" \
  -d @/home/ubuntu/aigateway/tests/test-gemini.json
```
```http
HTTP/1.1 200 OK
X-Gemini-Service-Tier: standard
Content-Type: application/json; charset=UTF-8
Content-Length: 353

{ "id": "chatcmpl-3yL7arW7H56G_uMP_5X4-AY", "object": "chat.completion", "created": 1794843123, "model": "gemini-flash-latest", "choices": [ { "index": 0, "message": { "role": "assistant", "content": "Computers learn patterns from data." }, "finish_reason": "stop" } ], "usage": { "prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18 } }
```

### 10.2 Customized Policy & Governance Enforcement

**1. Unauthorized Model Access Enforcement (HTTP 403):**
Requesting an unscoped model (`gpt-4o-mini`):
```http
HTTP/1.1 403 Forbidden
Content-Type: application/json
Content-Length: 115

{"error":{"message":"Model not allowed for this virtual key","type":"model_not_allowed","param":null,"code":"403"}}
```

**2. Invalid Virtual Key Enforcement (HTTP 401):**
```http
HTTP/1.1 401 Unauthorized
Content-Type: application/json
Content-Length: 100

{"error":{"message":"Invalid virtual key","type":"token_not_found_in_db","param":null,"code":"401"}}
```

**3. OpenTelemetry Inference Telemetry in ClickHouse:**
```sql
SELECT 
    timestamp, request_id, principal_id, team_id, org_id,
    deployment_upstream, gen_ai.usage.input_tokens, 
    gen_ai.usage.output_tokens, gen_ai.f5.usage.total_tokens,
    gen_ai.f5.latency_ms, gen_ai.f5.latency_gate_us, 
    http.response.status_code
FROM otel.aigw_requests
ORDER BY ts_ms DESC LIMIT 1 FORMAT Vertical;
```
```text
timestamp:                    2026-11-16 15:32:03.654
request_id:                   68fb5c034b60b44e06e485c30cb9fd3b
principal_id:                 demo-app
team_id:                      t-ec30b88defa0d17c
org_id:                       demo-org
deployment_upstream:          gemini-3.5-flash-lite
gen_ai.usage.input_tokens:    12
gen_ai.usage.output_tokens:   6
gen_ai.f5.usage.total_tokens: 18
gen_ai.f5.latency_ms:         2415
gen_ai.f5.latency_gate_us:    2916
http.response.status_code:    200
```
- Gateway routing overhead (`latency_gate_us`): ~2.9–3.7 ms.
- Spend calculation and token counts tracked in real-time.
