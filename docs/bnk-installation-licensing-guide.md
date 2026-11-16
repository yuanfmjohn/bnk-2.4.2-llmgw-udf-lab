# F5 BIG-IP Next for Kubernetes (BNK) v2.4.1 Installation & Licensing Guide

This document provides an end-to-end operational guide for installing, configuring, and licensing **F5 BIG-IP Next for Kubernetes (BNK) Host Edition v2.4.1** using the **F5 Lifecycle Operator (FLO)** on a self-managed Kubernetes cluster with Multus CNI macvlan networking.

---

## 1. Architecture & Environment Overview

### Cluster Topology & Components
* **Kubernetes Version:** `v1.35.9` (containerd runtime `2.2.3`, OS Ubuntu 24.04 LTS)
* **Helm Version:** `v4.3.1`
* **Primary CNI:** Project Calico (eBPF / standard overlay on `192.168.0.0/16`)
* **Secondary CNI:** Multus CNI with `macvlan` in `passthru` mode
* **F5 Lifecycle Operator (FLO):** Chart version `v2.30.0-0.11.4` (appVersion `2.1.0-2.30.0+0.11.4`)
* **BNK Manifest Version:** `2.4.1`

```
  ┌────────────────────────────────────────────────────────────────────────┐
  │                           Kubernetes Cluster                           │
  │                                                                        │
  │  ┌──────────────┐   ┌───────────────────────┐   ┌───────────────────┐  │
  │  │  controller  │   │ worker2 / worker4     │   │ worker1 / worker3 │  │
  │  │ (Control-Pl.)│   │ (Platform / Operators)│   │ (TMM Data Plane)  │  │
  │  └──────────────┘   └───────────────────────┘   └───────────────────┘  │
  │         │                       │                         │            │
  │         │             ┌──────────────────┐      ┌──────────────────┐   │
  │         │             │   f5-cne-core    │      │ f5-bnk-instance  │   │
  │         │             │  - FLO Operator  │      │  - f5-tmm (7/7)  │   │
  │         │             │  - CWC           │      │  - f5-cne-ctlr   │   │
  │         │             │  - RabbitMQ      │      │  - f5-afm        │   │
  │         │             │  - toda-fluentd  │      │  - f5-dssm-db    │   │
  │         │             │  - License CR    │      │  - dssm-sentinel │   │
  │         │             └──────────────────┘      └──────────────────┘   │
  │         │                                                 │            │
  │         │             Multus Secondary Network (macvlan)  │            │
  │         └─────────────────────────────────────────────────┼────────────┤
  │                                                           │            │
  │                                  ens6 (external) ─────────┼─ net1      │
  │                                  ens7 (internal) ─────────┼─ net2      │
  │                                  ens8 (cnf) ──────────────┴─ net3      │
  └────────────────────────────────────────────────────────────────────────┘
```

### Node Roles & Workload Distribution
* **`controller` (`10.1.1.12`):** Kubernetes Control Plane.
* **`worker1` (`10.1.1.6`) & `worker3` (`10.1.1.8`):** Designated **F5 TMM Data Plane Nodes**. Equipped with 4Gi hugepages and labeled/annotated for TMM scheduling.
* **`worker2` (`10.1.1.7`) & `worker4` (`10.1.1.9`):** Platform nodes running FLO, CWC, DSSM DB/Sentinel, CNE controllers, and telemetry collectors.

---

## 2. Prerequisites & Infrastructure Setup

### Step 2.1: Hugepages Allocation on TMM Nodes
BNK TMM requires 2MB hugepages reserved in advance on each data plane node.

On `worker1` and `worker3`:
```bash
# Allocate 2048 x 2MB hugepages (4096MB = 4Gi)
sudo sysctl -w vm.nr_hugepages=2048
echo "vm.nr_hugepages=2048" | sudo tee -a /etc/sysctl.conf

# Verify hugepages availability
grep -i huge /proc/meminfo
```

### Step 2.2: Annotate and Label TMM Nodes
FLO and the BNK daemonset target nodes with specific labels and annotations:

```bash
# Label worker1 and worker3
kubectl label node worker1 app=f5-tmm f5-tmm=node --overwrite
kubectl label node worker3 app=f5-tmm f5-tmm=node --overwrite

# Annotate worker1 and worker3
kubectl annotate node worker1 app=f5-tmm f5-tmm=node --overwrite
kubectl annotate node worker3 app=f5-tmm f5-tmm=node --overwrite
```

### Step 2.3: Multus CNI, Host Secondary NICs & Macvlan NetworkAttachmentDefinitions

1. **Ensure Secondary NICs (`ens6`, `ens7`, `ens8`) are UP on TMM Nodes (`worker1`, `worker3`):**
   Multus `macvlan` (`passthru`) interfaces cannot pass traffic if the parent host NIC is administratively `DOWN`. Configure `/etc/netplan/99-extra-nics.yaml` on `worker1` and `worker3` and verify link state:
   ```yaml
   network:
     version: 2
     renderer: networkd
     ethernets:
       ens6:
         dhcp4: false
         dhcp6: false
         optional: true
       ens7:
         dhcp4: false
         dhcp6: false
         optional: true
       ens8:
         dhcp4: false
         dhcp6: false
         optional: true
   ```
   ```bash
   sudo netplan apply
   sudo ip link set ens6 up && sudo ip link set ens7 up && sudo ip link set ens8 up
   ```

2. **Deploy Multus CNI DaemonSet:**
   ```bash
   kubectl apply -f https://raw.githubusercontent.com/k8snetworkplumbingwg/multus-cni/master/deployments/multus-daemonset.yml
   kubectl wait --for=condition=Ready pod -l app=multus -n kube-system --timeout=120s
   ```

3. **Create Namespaces:**
   ```bash
   kubectl create namespace f5-cne-core --dry-run=client -o yaml | kubectl apply -f -
   kubectl create namespace f5-bnk-instance --dry-run=client -o yaml | kubectl apply -f -
   ```

4. **Deploy NetworkAttachmentDefinitions (NADs):**
   Apply across `default`, `f5-cne-core`, and `f5-bnk-instance`:

   ```yaml
   apiVersion: "k8s.cni.cncf.io/v1"
   kind: NetworkAttachmentDefinition
   metadata:
     name: external-net
     namespace: f5-bnk-instance
   spec:
     config: '{
       "cniVersion": "0.3.1",
       "type": "macvlan",
       "master": "ens6",
       "mode": "passthru"
     }'
   ---
   apiVersion: "k8s.cni.cncf.io/v1"
   kind: NetworkAttachmentDefinition
   metadata:
     name: internal-net
     namespace: f5-bnk-instance
   spec:
     config: '{
       "cniVersion": "0.3.1",
       "type": "macvlan",
       "master": "ens7",
       "mode": "passthru"
     }'
   ---
   apiVersion: "k8s.cni.cncf.io/v1"
   kind: NetworkAttachmentDefinition
   metadata:
     name: cnf-net
     namespace: f5-bnk-instance
   spec:
     config: '{
       "cniVersion": "0.3.1",
       "type": "macvlan",
       "master": "ens8",
       "mode": "passthru"
     }'
   ```

### Step 2.4: Deploy `cert-manager` & ClusterIssuers
BNK internal gRPC microservices and webhooks require TLS certificates generated by `cert-manager`.

1. **Install cert-manager v1.19.4:**
   ```bash
   kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.19.4/cert-manager.yaml
   kubectl wait --for=condition=Available deployment --all -n cert-manager --timeout=180s
   ```

2. **Configure Internal CA ClusterIssuers:**
   ```yaml
   apiVersion: cert-manager.io/v1
   kind: ClusterIssuer
   metadata:
     name: selfsigned-cluster-issuer
   spec:
     selfSigned: {}
   ---
   apiVersion: cert-manager.io/v1
   kind: Certificate
   metadata:
     name: f5-cne-ca-certificate
     namespace: cert-manager
   spec:
     isCA: true
     commonName: f5-cne-ca
     secretName: f5-cne-ca-secret
     issuerRef:
       name: selfsigned-cluster-issuer
       kind: ClusterIssuer
       group: cert-manager.io
   ---
   apiVersion: cert-manager.io/v1
   kind: ClusterIssuer
   metadata:
     name: f5-cne-internal-ca
   spec:
     ca:
       secretName: f5-cne-ca-secret
   ```

### Step 2.5: Storage Class Provisioning (CSI Driver NFS)
BNK components (dSSM database, fluentd, logs, observer) require persistent storage.

1. **Install CSI Driver NFS:**
   ```bash
   helm repo add csi-driver-nfs https://raw.githubusercontent.com/kubernetes-csi/csi-driver-nfs/master/charts
   helm repo update
   helm install csi-driver-nfs csi-driver-nfs/csi-driver-nfs \
     --namespace kube-system \
     --set kubeletDir=/var/lib/kubelet
   ```

2. **Configure NFS Server Export:**
   On the NFS server host (`10.1.1.4`):
   ```bash
   # In /etc/exports, set no_root_squash to support container user ID mappings:
   /mnt/nfs_share *(rw,sync,no_subtree_check,no_root_squash)
   sudo exportfs -rav
   ```

3. **Define StorageClass `nfs`:**
   ```yaml
   apiVersion: storage.k8s.io/v1
   kind: StorageClass
   metadata:
     name: nfs
     annotations:
       storageclass.kubernetes.io/is-default-class: "true"
   provisioner: nfs.csi.k8s.io
   parameters:
     server: 10.1.1.4
     share: /mnt/nfs_share
   reclaimPolicy: Delete
   volumeBindingMode: Immediate
   ```

### Step 2.6: Create Registry Pull Secrets
Create `far-pull-secret` (containing credentials for `repo.f5.com`) in all target namespaces:

```bash
kubectl create secret docker-registry far-pull-secret \
  --docker-server=repo.f5.com \
  --docker-username="<F5_USERNAME>" \
  --docker-password="<F5_PASSWORD>" \
  --namespace=default

kubectl get secret far-pull-secret -n default -o yaml | sed 's/namespace: default/namespace: f5-cne-core/' | kubectl apply -f -
kubectl get secret far-pull-secret -n default -o yaml | sed 's/namespace: default/namespace: f5-bnk-instance/' | kubectl apply -f -
```

---

## 3. Deploy F5 Lifecycle Operator (FLO)

### Step 3.1: Prepare Override Values
Create `f5-lifecycle-operator-override-values.yaml`:

```yaml
global:
  imagePullSecrets:
  - name: far-pull-secret

ServiceIPFamily: ipv4

image:
  repository: repo.f5.com/images
  pullPolicy: IfNotPresent

containerPlatform: Generic

resources:
  limits:
    cpu: "0.5"
    memory: "512Mi"
  requests:
    cpu: "0.25"
    memory: "256Mi"

affinity: {}
tolerations: []
```

### Step 3.2: Install FLO with Helm
Using Helm v4:

```bash
helm install f5-lifecycle-operator f5-lifecycle-operator-v2.30.0-0.11.4.tgz \
  --namespace f5-cne-core \
  -f f5-lifecycle-operator-override-values.yaml
```

Wait until FLO is ready:
```bash
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=f5-lifecycle-operator -n f5-cne-core --timeout=120s
```

---

## 4. Deploy CNEInstance Custom Resource

The `CNEInstance` CR defines the BNK deployment characteristics, enabling TMM, dynamic routing, AFM firewalls, and secondary network attachments.

### Step 4.1: Manifest Definition (`cne-instance-f5-bnk-instance.yaml`)
```yaml
apiVersion: k8s.f5.com/v1
kind: CNEInstance
metadata:
  name: f5-bnk-instance
  namespace: f5-bnk-instance
spec:
  product:
    gatewayAPI: true
    type: BNK
  manifestVersion: 2.4.1
  wholeCluster: true
  dpu:
    enabled: false 
  telemetry:
    loggingSubsystem:
      enabled: true
    metricSubsystem:
      enabled: true
  certificate:
    clusterIssuer: f5-cne-internal-ca
  deploymentSize: Small 
  registry:
    uri: repo.f5.com
    imagePullSecrets:
    - name: far-pull-secret
    imagePullPolicy: IfNotPresent
  networkAttachments:
  - external-net
  - internal-net
  - cnf-net
  storageClassName: nfs
  dynamicRouting:
    enabled: true
  firewallACL:
    enabled: true
  pseudoCNI:
    enabled: true
  coreCollection:
    enabled: true
  advanced:
    envDiscovery:
      enabled: false 
      stopOnFail: true
      runAfterSuccess: true
    cneController:
      env:
      - name: "GSLB_DATACENTER_NAME"
        value: "DC1"
    demoMode:
      enabled: true
    maintenanceMode:
      enabled: false
    tmm:
      env:
      - name: TMM_CALICO_ROUTER
        value: default
      - name: TMM_DEFAULT_MTU
        value: "9000"
      - name: ZEBOS_STATE
        value: legacy
      - name: "TMM_MAPRES_ADDL_VETHS_ON_DP"
        value: "TRUE"
    pseudoCNI:
      env:
      - name: EXCLUDE_CIDR
        value: "10.1.1.0/8,10.1.200.0/24,10.1.210.0/24,10.1.220.0/24,10.96.0.0/12,192.168.0.0/16"
```

### Step 4.2: Apply the CNEInstance
```bash
kubectl apply -f cne-instance-f5-bnk-instance.yaml -n f5-bnk-instance
```

FLO will automatically:
1. Run `crd-installer` in `f5-cne-core` to register all BNK CRDs.
2. Deploy the core infrastructure pods into `f5-cne-core`.
3. Spin up `f5-cne-controller`, `f5-dssm-db`, `f5-dssm-sentinel`, and `f5-tmm` pods in `f5-bnk-instance`.

---

## 5. License the BNK Cluster

BNK licensing uses the connected License Controller (CWC) to register against F5's licensing endpoint via a JWT evaluation or production entitlement token.

### Step 5.1: License Manifest (`f5-cne-cluster-license.yaml`)
```yaml
apiVersion: k8s.f5net.com/v1
kind: License
metadata:
  name: f5-cne-cluster-license
  namespace: f5-cne-core
spec:
  operationMode: "connected"
  jwt: "<YOUR_F5_JWT_TOKEN_STRING>"
```

### Step 5.2: Apply the License
```bash
kubectl apply -f f5-cne-cluster-license.yaml -n f5-cne-core
```

### Step 5.3: Verify License State
```bash
kubectl get license -A
```

Expected output:
```text
NAMESPACE     NAME                     STATE    MODE        ENTITLEMENT   ENVIRONMENT   EXPIRY                 DIGITALASSETID                         AGE
f5-cne-core   f5-cne-cluster-license   Active   connected   eval          production    2026-11-20T00:13:31Z   62cc496c-196d-4566-8004-f95292a15239   5m
```

---

## 6. Verification & Health Checks

### Step 6.1: Check Pod Health
Execute across both namespaces:

```bash
kubectl get pods -n f5-cne-core
kubectl get pods -n f5-bnk-instance -o wide
```

Key pods that must be **Running**:
- `f5-cne-core`: `f5-lifecycle-operator` (1/1), `f5-spk-cwc` (3/3), `f5-rabbit` (2/2), `f5-spk-csrc` (2/2 per node), `f5-coremond` (2/2 per node).
- `f5-bnk-instance`: `f5-cne-controller` (3/3), `f5-afm` (2/2), `f5-dssm-db-[0..2]` (3/3), `f5-dssm-sentinel-[0..2]` (3/3), `f5-tmm` (7/7 on worker1 and worker3).

### Step 6.2: Validate TMM Multus Interface Attachments
Check the `k8s.v1.cni.cncf.io/network-status` annotation on the TMM pods:

```bash
kubectl get pod -l app=f5-tmm -n f5-bnk-instance -o jsonpath="{range .items[*]}{.metadata.name}{\"\n\"}{.metadata.annotations.k8s\.v1\.cni\.cncf\.io/network-status}{\"\n---\n\"}{end}"
```

Each TMM pod must report four interfaces:
- `eth0`: `k8s-pod-network` (cluster overlay)
- `net1`: `f5-bnk-instance/external-net` (bound to physical interface `ens6`)
- `net2`: `f5-bnk-instance/internal-net` (bound to physical interface `ens7`)
- `net3`: `f5-bnk-instance/cnf-net` (bound to physical interface `ens8`)

---

## 7. Troubleshooting & Common Issues

| Issue | Root Cause | Resolution |
| :--- | :--- | :--- |
| **`Helm template validation failed`** | Helm v4 client dry-run cannot evaluate cert-manager lookup functions before CRDs exist. | Run `--dry-run=server` instead of standard client dry-run. |
| **`TMM Pod stuck in Pending`** | Node hugepages missing or lack of 2MB blocks. | Ensure `sysctl vm.nr_hugepages=2048` is set and `f5-tmm=node` label exists. |
| **`TMM external-vlan ARP INCOMPLETE`** | Parent host NICs (`ens6`, `ens7`, `ens8`) on `worker1`/`worker3` are `DOWN` after boot. | Fix `/etc/netplan/99-extra-nics.yaml` and run `sudo ip link set ens6 up && sudo ip link set ens7 up && sudo ip link set ens8 up`. |
| **`f5-dssm-db-* 2/3 Running (SSL_connect failed)`** | 1-hour cert-manager TLS certificate rotated on disk, while `redis-server` still holds the expired cert in memory. | Restart the unready `f5-dssm-db-*` pod(s) via `kubectl delete pod <pod> -n f5-bnk-instance`. |
| **`License stuck in Registering`** | Outbound access to `product.apis.f5.com` blocked, or missing CWC license secrets. | Check egress connectivity and ensure `cwc-license-certs` secret is present in `f5-cne-core`. |
| **`NFS Volume Mount / chown EPERM`** | NFS share configured with `all_squash` or `root_squash`. | Update `/etc/exports` on NFS host to `no_root_squash` and reload with `sudo exportfs -rav`. |
