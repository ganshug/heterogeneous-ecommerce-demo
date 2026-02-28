# IBM Power E-Cart — Heterogeneous OCP Hosted Control Plane Demo

This solution demonstrates a **true heterogeneous e-commerce workload** on an OpenShift Hosted Control Plane (HCP) cluster with mixed-architecture worker nodes — Intel (x86_64) and IBM Power (ppc64le) — running on **IBM Fusion HCI**.

| Component | Architecture | Node Type | Workload | Image Source |
|-----------|-------------|-----------|----------|--------------|
| **E-Cart App Server** | `x86_64` | Intel | Flask e-commerce shopping cart | OCP internal registry (S2I build on amd64) |
| **IBM Db2 Community Edition** | `ppc64le` | IBM Power | Data persistence (products, cart, orders) | `cp.icr.io/cp/db2/db2u:latest` |

The e-cart app server (running on Intel) connects **cross-architecture** to IBM Db2 (running on IBM Power) via Kubernetes ClusterIP DNS — proving seamless heterogeneous workload communication on IBM Fusion HCI.

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│               OpenShift Hosted Control Plane (HCP)                   │
│                     Namespace: db2-shop-demo                          │
│                                                                       │
│  ┌──────────────────────────────┐   ┌──────────────────────────┐    │
│  │   Intel Worker Node          │   │   IBM Power Worker Node  │    │
│  │   (x86_64 / amd64)           │   │   (ppc64le)              │    │
│  │                              │   │                          │    │
│  │  ┌──────────────────────┐   │   │  ┌────────────────────┐  │    │
│  │  │  IBM Power E-Cart    │   │   │  │  IBM Db2 CE        │  │    │
│  │  │  Flask / Python      │───┼───┼─▶│  cp.icr.io/db2u   │  │    │
│  │  │  Port: 8080          │   │   │  │  Port: 50000       │  │    │
│  │  │  (S2I / UBI9 amd64)  │   │   │  │  (multi-arch)      │  │    │
│  │  └──────────────────────┘   │   │  └────────────────────┘  │    │
│  │         │                   │   │                          │    │
│  │  shop-cart-service           │   │  db2-service              │    │
│  └──────────────────────────────┘   └──────────────────────────┘    │
│         │                                                             │
│  OCP Route (TLS edge)                                                │
└─────────┼─────────────────────────────────────────────────────────── ┘
          │
    External Users
    https://shop-cart-route-db2-shop-demo.<apps-domain>
```

---

## Image Sources

| Component | Image | Source |
|-----------|-------|--------|
| IBM Db2 CE | `cp.icr.io/cp/db2/db2u:latest` | IBM Container Registry (multi-arch: amd64 + ppc64le) |
| E-Cart App | `image-registry.openshift-image-registry.svc:5000/db2-shop-demo/shop-cart-app:latest` | OCP internal registry (S2I build) |
| S2I base image | `registry.redhat.io/ubi9/python-311:latest` | Red Hat registry (multi-arch) |
| Init container | `image-registry.openshift-image-registry.svc:5000/openshift/cli:latest` | OCP internal registry |

> **Note:** IBM Entitlement Key must be configured in the OCP global pull secret to pull from `cp.icr.io`.

---

## File Structure

```
hetero-db2-demo/
├── README.md                        # This file
├── deploy.sh                        # One-shot deployment script
├── 00-namespace.yaml                # Namespace: db2-shop-demo
├── 01-node-labels-taints.sh         # Label Intel (appserver) and Power (database) nodes
├── 02-db2-deployment.yaml           # IBM Db2 CE Deployment → IBM Power (ppc64le) node
├── 03-appserver-build.yaml          # OCP S2I BuildConfig + ImageStream (amd64 build)
├── 04-appserver-deployment.yaml     # E-Cart App Server → Intel (x86_64) node
├── 05-appserver-service-route.yaml  # Service + OCP Route for E-Cart app
├── 06-network-policy.yaml           # NetworkPolicy for cross-arch traffic
└── app/
    ├── app.py                       # Flask e-commerce shopping cart application
    ├── requirements.txt             # Python dependencies (Flask + ibm_db)
    └── Dockerfile                   # Dockerfile using registry.redhat.io/ubi9/python-311
```

---

## Prerequisites

- OpenShift HCP guest cluster (on IBM Fusion HCI or any OCP cluster) with:
  - At least **1 Intel (x86_64)** worker node
  - At least **1 IBM Power (ppc64le)** worker node
- `oc` CLI logged in to the HCP guest cluster
- **IBM Entitlement Key** configured in the OCP global pull secret:
  ```bash
  # Verify the global pull secret includes cp.icr.io
  oc get secret/pull-secret -n openshift-config -o jsonpath='{.data.\.dockerconfigjson}' | base64 -d | python3 -m json.tool | grep cp.icr.io
  ```
- Internal OCP image registry accessible (default in OCP clusters)

---

## Quick Start — One-Shot Deployment

```bash
# Clone the repo
git clone https://github.com/ganshug/heterogeneous-ocp-demo.git
cd heterogeneous-ocp-demo/hetero-db2-demo

# Log in to your OCP HCP cluster
oc login <api-url> --token=<token>

# Label your nodes (edit the script first — set INTEL_NODE and POWER_NODE)
vi 01-node-labels-taints.sh
bash 01-node-labels-taints.sh

# Deploy everything
bash deploy.sh
```

---

## Step-by-Step Deployment

### Step 1 — Label the nodes

Find your node names:
```bash
oc get nodes -o wide
```

Edit `01-node-labels-taints.sh` and set:
```bash
INTEL_NODE="<your-intel-node-hostname>"
POWER_NODE="<your-power-node-hostname>"
```

Run the labeling script:
```bash
bash 01-node-labels-taints.sh
```

Verify labels:
```bash
oc get nodes --show-labels | grep workload-type
```

---

### Step 2 — Create namespace

```bash
oc apply -f 00-namespace.yaml
```

---

### Step 3 — Deploy IBM Db2 on IBM Power node

```bash
oc apply -f 02-db2-deployment.yaml

# Watch the pod come up on the Power node (Db2 takes 3-5 minutes to initialize)
oc get pods -n db2-shop-demo -l app=db2 -o wide -w
```

This creates:
- **Secret** `db2-secret` — Db2 credentials (user, password, dbname, connection URL)
- **PVC** `db2-data-pvc` — 20Gi data volume on Power node
- **Deployment** `db2` — pinned to ppc64le via nodeAffinity
- **Service** `db2-service` — ClusterIP on port 50000

> **Note:** IBM Db2 requires privileged access for kernel parameter tuning. The deployment uses `securityContext.privileged: true`.

---

### Step 4 — Build the E-Cart App Server (S2I on Intel node)

```bash
# Create the BuildConfig and ImageStream
oc apply -f 03-appserver-build.yaml

# Start the S2I build (uploads app/ directory to the cluster)
# The build pod is pinned to the amd64 (Intel) node via nodeSelector
oc start-build shop-cart-app --from-dir=./app/ --follow -n db2-shop-demo
```

The built image is stored in the internal OCP registry:
`image-registry.openshift-image-registry.svc:5000/db2-shop-demo/shop-cart-app:latest`

To rebuild after code changes:
```bash
oc start-build shop-cart-app --from-dir=./app/ --follow --wait -n db2-shop-demo
oc rollout restart deployment/shop-cart -n db2-shop-demo
oc rollout status deployment/shop-cart -n db2-shop-demo
```

---

### Step 5 — Deploy E-Cart App Server on Intel node

```bash
oc apply -f 04-appserver-deployment.yaml
oc apply -f 05-appserver-service-route.yaml
oc apply -f 06-network-policy.yaml

# Wait for rollout
oc rollout status deployment/shop-cart -n db2-shop-demo
```

---

### Step 6 — Verify deployment

```bash
# Check pod placement
oc get pods -n db2-shop-demo -o wide
```

Expected output:
```
NAME                        READY  STATUS   NODE                                        ...
db2-xxxx                    1/1    Running  <your-power-node-hostname>   ...
shop-cart-xxxx              1/1    Running  <your-intel-node-hostname>   ...
```

Confirm architectures:
```bash
# Db2 pod — should show ppc64le
oc exec -n db2-shop-demo deploy/db2 -- uname -m
# Expected: ppc64le

# E-Cart app pod — should show x86_64
oc exec -n db2-shop-demo deploy/shop-cart -- uname -m
# Expected: x86_64
```

---

### Step 7 — Test the application

Get the Route URL:
```bash
ROUTE=$(oc get route shop-cart-route -n db2-shop-demo -o jsonpath='{.spec.host}')
echo "App URL: https://$ROUTE"
```

Open the browser UI:
```
https://<ROUTE>/
```

Test the REST API:
```bash
# Health check
curl -sk https://$ROUTE/health

# Readiness check (includes Db2 connectivity)
curl -sk https://$ROUTE/ready

# Show architecture info (Intel app → Power Db2)
curl -sk https://$ROUTE/arch | python3 -m json.tool

# List all products (from IBM Db2 on IBM Power)
curl -sk https://$ROUTE/products | python3 -m json.tool

# View current cart
curl -sk https://$ROUTE/cart | python3 -m json.tool

# View placed orders
curl -sk https://$ROUTE/orders | python3 -m json.tool
```

Expected `/arch` response:
```json
{
  "heterogeneous_demo": {
    "app_server": {
      "role": "IBM Power E-Cart Application Server",
      "architecture": "x86_64",
      "arch_label": "x86_64 (Intel)",
      "node": "<your-intel-node-hostname>"
    },
    "database": {
      "role": "IBM Db2 Community Edition",
      "architecture": "ppc64le (IBM Power)",
      "host": "db2-service.db2-shop-demo.svc.cluster.local",
      "port": 50000,
      "connected": true,
      "db2_version": "IBM Db2 DB2 v11.5.x.x ...",
      "image": "cp.icr.io/cp/db2/db2u:latest"
    }
  }
}
```

---

## How Workload Placement Works

### Node Labels (set by `01-node-labels-taints.sh`)

| Node | Architecture | `workload-type` label | Workload |
|------|-------------|----------------------|----------|
| Intel worker | `x86_64` / `amd64` | `appserver` | E-Cart App Server |
| Power worker | `ppc64le` | `database` | IBM Db2 CE |

### Node Affinity (Hard Placement)

**IBM Db2 → IBM Power node:**
```yaml
affinity:
  nodeAffinity:
    requiredDuringSchedulingIgnoredDuringExecution:
      nodeSelectorTerms:
        - matchExpressions:
            - key: kubernetes.io/arch
              operator: In
              values: [ppc64le]
            - key: workload-type
              operator: In
              values: [database]
```

**E-Cart App Server → Intel node:**
```yaml
affinity:
  nodeAffinity:
    requiredDuringSchedulingIgnoredDuringExecution:
      nodeSelectorTerms:
        - matchExpressions:
            - key: kubernetes.io/arch
              operator: In
              values: [amd64]
            - key: workload-type
              operator: In
              values: [appserver]
```

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Browser UI — IBM Power E-Cart shopping interface |
| GET | `/health` | Liveness probe |
| GET | `/ready` | Readiness probe (checks Db2 connectivity) |
| GET | `/arch` | **Cross-arch info** — shows Intel app + Power Db2 details |
| GET | `/products` | List all products from IBM Db2 |
| GET | `/cart` | View current cart contents |
| GET | `/orders` | List placed orders |

---

## Cleanup

```bash
oc delete namespace db2-shop-demo
```

---

## Key Takeaways

1. **IBM Db2 from `cp.icr.io`** — IBM Container Registry provides multi-arch Db2 images; the ppc64le variant runs natively on IBM Power
2. **IBM Entitlement Key in global pull secret** — no namespace-level imagePullSecrets needed
3. **OCP S2I BuildConfig** with `nodeSelector: kubernetes.io/arch: amd64` builds the e-cart image natively on the Intel node
4. **`kubernetes.io/arch`** label is auto-applied by OCP — use it for architecture-based scheduling
5. **Node affinity** with `requiredDuringScheduling` enforces hard placement
6. **ClusterIP DNS** works transparently across architectures — `db2-service.db2-shop-demo.svc.cluster.local` resolves correctly from the Intel node
7. **ibm_db_dbi** provides a PEP-249 DB-API 2.0 interface to Db2 — familiar cursor-based API
8. **Cross-arch verified**: Intel (x86_64) e-cart app reads/writes products, cart items, and orders to IBM Db2 running on IBM Power (ppc64le)