# F5 BIG-IP Next for Kubernetes (BNK) v2.4 - AI Gateway Lab Guide

This repository contains the validated deployment guides, Kubernetes manifests, governance automation scripts, and end-to-end verification assets for running the **F5 BIG-IP Next for Kubernetes (BNK) v2.4 AI Gateway** in a UDF lab cluster.

## Repository Contents

- **[BNK Installation & Licensing Guide](docs/bnk-installation-licensing-guide.md)** — Cluster prerequisites (hugepages, Multus `macvlan` passthru, `cert-manager`, CSI NFS), F5 Lifecycle Operator (FLO) deployment, `CNEInstance` setup, F5 connected licensing, and `F5SPKVlan` / `F5SPKStaticRoute` data-plane configuration.
- **[BNK AI Gateway Deployment & Operations Guide](docs/bnk-aigateway-deployment-guide.md)** — Enabling `aiGateway` on `CNEInstance`, `GatewayClass` and `F5BigDnsCache` integration, end-to-end Path 3 deployment (without `ocibnkctl`), governance & virtual keys, ClickHouse OpenTelemetry verification, and lab troubleshooting.
- **[AI Gateway Lab Assets & Step-by-Step Walkthrough](aigateway/README.md)** — Sequentially numbered manifests (`00`–`09`), Google AI Studio (Gemini) primary configuration (`aigateway/gemini/`), NVIDIA NGC alternative provider option (`02`–`06`), governance bootstrap script (`aigateway/governance/setup_keys.py`), and client test suite (`aigateway/tests/run_test.sh`).
- **[AI Inference Load Balancing Manifests](ai-loadbalance/)** — Reference Gateway API and DCGM GPU scrape manifests for backend inference load balancing.

## Quick Validation Summary

1. **Cluster & Control Plane**: 5-node Kubernetes `v1.35.9` cluster (`controller`, `worker1`–`worker4`) with active F5 BNK evaluation license (`f5-cne-cluster-license` in `f5-cne-core`), `f5-tmm` on `worker1`/`worker3`, and `f5-dssm-db-[0..2]` (`3/3 Running`).
2. **Primary Verified Provider**: Google AI Studio (`generativelanguage.googleapis.com`) using `gemini-3.5-flash-lite` (`gemini-flash-latest` and `gemini-3.5-flash-lite` models), with NVIDIA NGC (`integrate.api.nvidia.com`) documented as an OpenAI-compatible alternative.
3. **End-to-End Verification** (from `client` at `10.1.10.4` against VIP `10.1.200.85`):
   - `GET /v1/models` -> `HTTP/1.1 200 OK`
   - `POST /v1/chat/completions` -> `HTTP/1.1 200 OK`
   - Governance enforcement -> `403 Forbidden` (`model_not_allowed`), `401 Unauthorized` (`token_not_found_in_db`)
   - Telemetry -> Verified in ClickHouse `otel.aigw_requests` (`principal_id`, `team_id`, `org_id`, token usage, and ~3 ms gateway overhead).
4. **Console Access & Admin Credentials**:
   - Console UI: `f5-aigateway-console` (`NodePort 3000:30964/TCP`, accessible at `http://<node-ip>:30964` or via UDF **`llmgw-console`** on **`worker4`**).
   - System Admin secret: `f5-aigateway-admin` (`username: admin`, password `********` via `kubectl get secret -n f5-bnk-instance`).
   - Tenant Admin secrets: `aitenant-demo-admin` and `aitenant-llmgw-demo-admin` (`username: admin`, passwords `********` via `kubectl get secret -n f5-bnk-instance`).
