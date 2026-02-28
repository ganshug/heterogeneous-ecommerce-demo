#!/usr/bin/env bash
# =============================================================================
# deploy.sh — IBM Power E-Cart Demo (Heterogeneous OCP HCP)
#
# Deploys:
#   1. IBM Operator Catalog (CatalogSource in openshift-marketplace)
#   2. Namespace: ecommerce-demo
#   3. IBM Entitlement Key secret (for pulling Db2 images)
#   4. IBM Db2 Operator (via OLM Subscription)
#   5. IBM Db2uCluster instance → IBM Power (ppc64le) node
#   6. E-Cart App Server (S2I build + Deployment) → Intel (x86_64) node
#   7. Service + Route for E-Cart app
#   8. NetworkPolicy
#
# Prerequisites:
#   - oc CLI logged in to OCP cluster as cluster-admin
#   - Intel (amd64) node labeled: workload-type=appserver
#   - IBM Power (ppc64le) node labeled: workload-type=database
#     (run: bash 01-node-labels-taints.sh to set labels)
#
# Required credentials (provide via env vars or interactive prompt):
#   IBM_ENTITLEMENT_KEY  — IBM Container Registry entitlement key
#                          from https://myibm.ibm.com/products-services/containerlibrary
#   DB2_LICENSE_FILE     — Path to IBM Db2 license file (.lic)
#                          Required for Db2uCluster to start successfully
#
# Usage:
#   # Option 1: Environment variables
#   export IBM_ENTITLEMENT_KEY="<your-key>"
#   export DB2_LICENSE_FILE="/path/to/db2/license.lic"
#   bash deploy.sh
#
#   # Option 2: Interactive prompts
#   bash deploy.sh
# =============================================================================

set -euo pipefail

NAMESPACE="ecommerce-demo"
DB2_CLUSTER_NAME="db2u-ecommerce"
APP_NAME="shop-cart-app"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC}   $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# =============================================================================
echo ""
echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║     IBM Power E-Cart — Heterogeneous OCP HCP Demo                   ║"
echo "║     IBM Db2 (ppc64le) + Flask E-Cart (x86_64) on IBM Fusion HCI     ║"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo ""

# =============================================================================
# Step 0: Verify prerequisites and collect credentials
# =============================================================================
info "Step 0: Verifying prerequisites and collecting credentials..."

oc whoami &>/dev/null || error "Not logged in to OCP cluster. Run: oc login <api-url> --token=<token>"
success "Logged in as: $(oc whoami)"

# Check nodes
INTEL_NODE=$(oc get nodes -l "kubernetes.io/arch=amd64,workload-type=appserver" -o name 2>/dev/null | head -1)
POWER_NODE=$(oc get nodes -l "kubernetes.io/arch=ppc64le,workload-type=database" -o name 2>/dev/null | head -1)

if [[ -z "$INTEL_NODE" ]]; then
  warn "No Intel (amd64) node with workload-type=appserver found."
  warn "Run: bash 01-node-labels-taints.sh"
  warn "Continuing anyway — app pod may remain Pending until labels are set."
else
  success "Intel node: $INTEL_NODE"
fi

if [[ -z "$POWER_NODE" ]]; then
  warn "No IBM Power (ppc64le) node with workload-type=database found."
  warn "Run: bash 01-node-labels-taints.sh"
  warn "Continuing anyway — Db2 pod may remain Pending until labels are set."
else
  success "IBM Power node: $POWER_NODE"
fi

# --- Collect IBM Entitlement Key ---
if [[ -z "${IBM_ENTITLEMENT_KEY:-}" ]]; then
  echo ""
  echo "  IBM Entitlement Key is required to pull IBM Db2 images from cp.icr.io"
  echo "  Obtain from: https://myibm.ibm.com/products-services/containerlibrary"
  echo ""
  read -rsp "  Enter IBM Entitlement Key (input hidden): " IBM_ENTITLEMENT_KEY
  echo ""
  [[ -z "$IBM_ENTITLEMENT_KEY" ]] && error "IBM Entitlement Key cannot be empty."
fi
success "IBM Entitlement Key provided"

# --- Collect IBM Db2 License File ---
if [[ -z "${DB2_LICENSE_FILE:-}" ]]; then
  echo ""
  echo "  IBM Db2 license file (.lic) is required for Db2uCluster to start."
  echo "  This is your IBM Db2 product license certificate file."
  echo ""
  read -rp "  Enter path to IBM Db2 license file: " DB2_LICENSE_FILE
  echo ""
fi

if [[ ! -f "$DB2_LICENSE_FILE" ]]; then
  error "Db2 license file not found: $DB2_LICENSE_FILE"
fi
success "Db2 license file: $DB2_LICENSE_FILE"

# Base64-encode the license file
DB2_LICENSE_B64=$(base64 -i "$DB2_LICENSE_FILE" 2>/dev/null || base64 "$DB2_LICENSE_FILE")
[[ -z "$DB2_LICENSE_B64" ]] && error "Failed to base64-encode Db2 license file."
success "Db2 license file encoded (${#DB2_LICENSE_B64} chars)"

# =============================================================================
# Step 1: IBM Operator Catalog
# =============================================================================
echo ""
info "Step 1: Installing IBM Operator Catalog..."
oc apply -f "${SCRIPT_DIR}/00-ibm-operator-catalog.yaml"

info "Waiting for IBM Operator Catalog to be ready (up to 120s)..."
for i in $(seq 1 24); do
  STATUS=$(oc get catalogsource ibm-operator-catalog -n openshift-marketplace \
    -o jsonpath='{.status.connectionState.lastObservedState}' 2>/dev/null || echo "")
  if [[ "$STATUS" == "READY" ]]; then
    success "IBM Operator Catalog is READY"
    break
  fi
  echo "  Waiting... ($((i*5))s) status=${STATUS:-pending}"
  sleep 5
  if [[ $i -eq 24 ]]; then
    warn "IBM Operator Catalog not ready after 120s — continuing anyway"
  fi
done

# =============================================================================
# Step 2: Namespace
# =============================================================================
echo ""
info "Step 2: Creating namespace ${NAMESPACE}..."
oc apply -f "${SCRIPT_DIR}/00-namespace.yaml"
success "Namespace ${NAMESPACE} ready"

# Grant privileged SCC to default SA (Db2 requires kernel param tuning)
info "Granting privileged SCC to default service account in ${NAMESPACE}..."
oc adm policy add-scc-to-user privileged \
  system:serviceaccount:${NAMESPACE}:default 2>/dev/null || true
success "SCC granted"

# =============================================================================
# Step 3: IBM Entitlement Key Secret
# =============================================================================
echo ""
info "Step 3: Creating IBM Entitlement Key secret in ${NAMESPACE}..."
oc create secret docker-registry ibm-entitlement-key \
  --docker-server=cp.icr.io \
  --docker-username=cp \
  --docker-password="${IBM_ENTITLEMENT_KEY}" \
  --namespace="${NAMESPACE}" \
  --dry-run=client -o yaml | oc apply -f -
success "IBM Entitlement Key secret created/updated"

# =============================================================================
# Step 4: IBM Db2 Operator (OLM)
# =============================================================================
echo ""
info "Step 4: Installing IBM Db2 Operator via OLM..."
oc apply -f "${SCRIPT_DIR}/02-db2-operator-group.yaml"
oc apply -f "${SCRIPT_DIR}/03-db2-subscription.yaml"

info "Waiting for Db2 Operator to be installed (up to 300s)..."
for i in $(seq 1 60); do
  CSV=$(oc get subscription ibm-db2u-operator -n ${NAMESPACE} \
    -o jsonpath='{.status.currentCSV}' 2>/dev/null || echo "")
  if [[ -n "$CSV" ]]; then
    CSV_STATUS=$(oc get csv "$CSV" -n ${NAMESPACE} \
      -o jsonpath='{.status.phase}' 2>/dev/null || echo "")
    if [[ "$CSV_STATUS" == "Succeeded" ]]; then
      success "Db2 Operator installed: $CSV"
      break
    fi
    echo "  Waiting for CSV $CSV... phase=${CSV_STATUS:-pending} ($((i*5))s)"
  else
    echo "  Waiting for subscription to resolve... ($((i*5))s)"
  fi
  sleep 5
  if [[ $i -eq 60 ]]; then
    warn "Db2 Operator not ready after 300s"
    oc get subscription ibm-db2u-operator -n ${NAMESPACE} 2>/dev/null || true
    oc get csv -n ${NAMESPACE} 2>/dev/null || true
  fi
done

# =============================================================================
# Step 5: IBM Db2uCluster instance on IBM Power node
# =============================================================================
echo ""
info "Step 5: Creating IBM Db2uCluster instance on IBM Power (ppc64le) node..."

# Inject the base64-encoded license into the Db2uCluster YAML (in-memory, not written to disk)
DB2_CLUSTER_YAML=$(sed "s|PLACEHOLDER_INJECTED_BY_DEPLOY_SCRIPT|${DB2_LICENSE_B64}|g" \
  "${SCRIPT_DIR}/05-db2u-cluster.yaml")
echo "$DB2_CLUSTER_YAML" | oc apply -f -
success "Db2uCluster CR applied with license"

info "Waiting for Db2uCluster to be ready (up to 600s — Db2 takes 5-10 min)..."
for i in $(seq 1 120); do
  STATE=$(oc get db2ucluster ${DB2_CLUSTER_NAME} -n ${NAMESPACE} \
    -o jsonpath='{.status.state}' 2>/dev/null || echo "")
  if [[ "$STATE" == "Ready" ]]; then
    success "Db2uCluster ${DB2_CLUSTER_NAME} is Ready!"
    break
  fi
  echo "  Waiting for Db2uCluster... state=${STATE:-pending} ($((i*5))s)"
  sleep 5
  if [[ $i -eq 120 ]]; then
    warn "Db2uCluster not ready after 600s — check pod logs:"
    warn "  oc logs c-${DB2_CLUSTER_NAME}-db2u-0 -n ${NAMESPACE} --tail=30"
    oc get db2ucluster ${DB2_CLUSTER_NAME} -n ${NAMESPACE} 2>/dev/null || true
    oc get pods -n ${NAMESPACE} -o wide 2>/dev/null | grep db2 || true
  fi
done

# Verify Db2 pod is on IBM Power node
DB2_POD=$(oc get pods -n ${NAMESPACE} -l "formation_id=${DB2_CLUSTER_NAME}" \
  -o name 2>/dev/null | grep "db2u-0" | head -1 || \
  oc get pods -n ${NAMESPACE} -o name 2>/dev/null | grep "db2u-0" | head -1)
if [[ -n "$DB2_POD" ]]; then
  DB2_NODE=$(oc get ${DB2_POD} -n ${NAMESPACE} \
    -o jsonpath='{.spec.nodeName}' 2>/dev/null || echo "unknown")
  DB2_ARCH=$(oc get node ${DB2_NODE} \
    -o jsonpath='{.metadata.labels.kubernetes\.io/arch}' 2>/dev/null || echo "unknown")
  success "Db2 pod running on node: ${DB2_NODE} (arch: ${DB2_ARCH})"
fi

# =============================================================================
# Step 6: Build E-Cart App Server (S2I on Intel node)
# =============================================================================
echo ""
info "Step 6: Building E-Cart App Server (S2I on Intel/amd64 node)..."
oc apply -f "${SCRIPT_DIR}/03-appserver-build.yaml"

info "Starting S2I build from app/ directory..."
oc start-build ${APP_NAME} \
  --from-dir="${SCRIPT_DIR}/app/" \
  --follow \
  --wait \
  -n ${NAMESPACE} || error "S2I build failed. Check: oc logs -n ${NAMESPACE} bc/${APP_NAME}"
success "S2I build completed — image stored in internal registry"

# =============================================================================
# Step 7: Deploy E-Cart App Server on Intel node
# =============================================================================
echo ""
info "Step 7: Deploying E-Cart App Server on Intel (x86_64) node..."
oc apply -f "${SCRIPT_DIR}/06-appserver-deployment.yaml"
oc apply -f "${SCRIPT_DIR}/07-appserver-service-route.yaml"
oc apply -f "${SCRIPT_DIR}/08-network-policy.yaml"

info "Waiting for E-Cart deployment rollout (up to 180s)..."
oc rollout status deployment/shop-cart -n ${NAMESPACE} --timeout=180s || \
  warn "Deployment rollout timed out — check: oc get pods -n ${NAMESPACE}"

# Verify app pod is on Intel node
APP_POD=$(oc get pods -n ${NAMESPACE} -l "app=shop-cart" -o name 2>/dev/null | head -1)
if [[ -n "$APP_POD" ]]; then
  APP_NODE=$(oc get ${APP_POD} -n ${NAMESPACE} \
    -o jsonpath='{.spec.nodeName}' 2>/dev/null || echo "unknown")
  APP_ARCH=$(oc get node ${APP_NODE} \
    -o jsonpath='{.metadata.labels.kubernetes\.io/arch}' 2>/dev/null || echo "unknown")
  success "E-Cart pod running on node: ${APP_NODE} (arch: ${APP_ARCH})"
fi

# =============================================================================
# Step 8: Summary
# =============================================================================
echo ""
echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║                    DEPLOYMENT SUMMARY                               ║"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo ""

ROUTE=$(oc get route shop-cart-route -n ${NAMESPACE} \
  -o jsonpath='{.spec.host}' 2>/dev/null || echo "not-found")

echo "  Namespace:    ${NAMESPACE}"
echo "  Db2 Cluster:  ${DB2_CLUSTER_NAME} (IBM Power / ppc64le)"
echo "  App Server:   shop-cart (Intel / x86_64)"
echo "  App URL:      https://${ROUTE}"
echo ""
echo "  Pod placement:"
oc get pods -n ${NAMESPACE} -o wide 2>/dev/null | \
  grep -v "^NAME" | \
  awk '{printf "    %-45s %-10s %s\n", $1, $3, $7}' || true
echo ""
echo "  Test commands:"
echo "    curl -sk https://${ROUTE}/health"
echo "    curl -sk https://${ROUTE}/ready"
echo "    curl -sk https://${ROUTE}/arch | python3 -m json.tool"
echo "    curl -sk https://${ROUTE}/products | python3 -m json.tool"
echo ""
success "Deployment complete!"