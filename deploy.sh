#!/bin/bash
# =============================================================================
# deploy.sh — Full deployment script for the IBM Power E-Cart Demo
#
# Architecture:
#   - App Server : IBM Power E-Cart (Flask/Python) — built via OCP S2I BuildConfig
#                  Base image: registry.redhat.io/ubi9/python-311:latest
#                  Build pod pinned to Intel (amd64) node → produces amd64 image
#                  Runs on Intel (x86_64) node
#
#   - Database   : IBM Db2 Community Edition
#                  Image: cp.icr.io/cp/db2/db2u:latest (multi-arch: amd64 + ppc64le)
#                  Runs on IBM Power (ppc64le) node
#                  IBM Entitlement Key must be in the OCP global pull secret
#
# Cross-arch flow: Intel (x86_64) E-Cart App → IBM Power (ppc64le) Db2
#
# Prerequisites:
#   - oc CLI logged in to your HCP guest cluster
#   - IBM Entitlement Key configured in OCP global pull secret
#     (oc get secret/pull-secret -n openshift-config)
#   - Nodes labeled:
#       Intel node  → workload-type=appserver
#       Power node  → workload-type=database
#     Run: bash 01-node-labels-taints.sh
#
# Usage:
#   bash deploy.sh
# =============================================================================

set -euo pipefail

NAMESPACE="db2-shop-demo"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---- Colour helpers ---------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ---- Preflight checks -------------------------------------------------------
info "Checking prerequisites..."
command -v oc >/dev/null 2>&1 || error "'oc' CLI not found. Please install and log in."
oc whoami      >/dev/null 2>&1 || error "Not logged in to OCP cluster. Run: oc login <api-url>"
success "Prerequisites OK. Logged in as: $(oc whoami)"

# ---- Step 1: Create namespace -----------------------------------------------
info "Step 1/6: Creating namespace '$NAMESPACE'..."
oc apply -f "${SCRIPT_DIR}/00-namespace.yaml"
success "Namespace ready."

# ---- Step 2: Node labels ----------------------------------------------------
info "Step 2/6: Node labeling check..."
warn "Ensure nodes are labeled before proceeding."
warn "If not done yet, run: bash 01-node-labels-taints.sh"
echo ""
info "Current nodes and architectures:"
oc get nodes -o custom-columns='NAME:.metadata.name,ARCH:.status.nodeInfo.architecture,STATUS:.status.conditions[-1].type,WORKLOAD:.metadata.labels.workload-type' 2>/dev/null || true
echo ""

# ---- Step 3: Deploy IBM Db2 on Power (ppc64le) node -------------------------
info "Step 3/6: Deploying IBM Db2 Community Edition on IBM Power (ppc64le) node..."
info "  Image: cp.icr.io/cp/db2/db2u:latest (multi-arch, IBM Entitlement Key in global pull secret)"
oc apply -f "${SCRIPT_DIR}/02-db2-deployment.yaml"

info "Waiting for Db2 pod to start (Db2 takes 3-5 minutes to initialize)..."
timeout=360
elapsed=0
while true; do
  READY=$(oc get deployment db2 -n "$NAMESPACE" \
    -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0")
  if [[ "$READY" == "1" ]]; then
    success "IBM Db2 is ready on IBM Power node."
    break
  fi
  info "  Waiting for Db2 (readyReplicas=${READY:-0}, ${elapsed}s elapsed)..."
  sleep 15
  elapsed=$((elapsed + 15))
  if [[ $elapsed -ge $timeout ]]; then
    warn "Timed out waiting for Db2. Check: oc get pods -n $NAMESPACE -l app=db2 -o wide"
    warn "Db2 may still be initializing. The app server will retry DB connection."
    break
  fi
done

# ---- Step 4: Build E-Cart App Server (S2I on Intel/amd64 node) --------------
info "Step 4/6: Building IBM Power E-Cart App via OCP S2I (amd64, internal registry)..."
info "  Base image: registry.redhat.io/ubi9/python-311:latest (no docker.io)"
info "  Build pod pinned to Intel (amd64) node → produces amd64 image"
oc apply -f "${SCRIPT_DIR}/03-appserver-build.yaml"

info "Starting S2I binary build from ${SCRIPT_DIR}/app/ ..."
oc start-build shop-cart-app \
  --from-dir="${SCRIPT_DIR}/app/" \
  --follow \
  --wait \
  -n "$NAMESPACE"
success "E-Cart app image built and pushed to internal ImageStream (amd64)."

# ---- Step 5: Deploy E-Cart App Server (Intel/amd64 node) --------------------
info "Step 5/6: Deploying IBM Power E-Cart App on Intel (x86_64) node..."
oc apply -f "${SCRIPT_DIR}/04-appserver-deployment.yaml"
oc apply -f "${SCRIPT_DIR}/05-appserver-service-route.yaml"
oc apply -f "${SCRIPT_DIR}/06-network-policy.yaml"
info "Waiting for E-Cart app to be ready..."
oc rollout status deployment/shop-cart -n "$NAMESPACE" --timeout=180s
success "IBM Power E-Cart is running on Intel (x86_64) node."

# ---- Step 6: Summary --------------------------------------------------------
echo ""
echo -e "${GREEN}============================================================${NC}"
echo -e "${GREEN}  IBM Power E-Cart Demo Deployed Successfully!${NC}"
echo -e "${GREEN}============================================================${NC}"
echo ""
ROUTE_HOST=$(oc get route shop-cart-route -n "$NAMESPACE" -o jsonpath='{.spec.host}' 2>/dev/null || echo "<route-not-found>")
echo -e "  App URL         : ${CYAN}https://${ROUTE_HOST}${NC}"
echo -e "  Architecture    : E-Cart App on ${YELLOW}Intel (x86_64 / amd64)${NC}"
echo -e "                    Database on   ${YELLOW}IBM Power (ppc64le)${NC}"
echo ""
echo -e "  Images used:"
echo -e "    IBM Db2       : cp.icr.io/cp/db2/db2u:latest (IBM Entitlement Key in global pull secret)"
echo -e "    E-Cart App    : image-registry.openshift-image-registry.svc:5000/db2-shop-demo/shop-cart-app:latest"
echo -e "    S2I base      : registry.redhat.io/ubi9/python-311:latest"
echo -e "    Init container: image-registry.openshift-image-registry.svc:5000/openshift/cli:latest"
echo ""
echo -e "  Test endpoints:"
echo -e "    Browser UI   : https://${ROUTE_HOST}/"
echo -e "    GET  https://${ROUTE_HOST}/arch      — cross-arch info (Intel app → Power Db2)"
echo -e "    GET  https://${ROUTE_HOST}/products  — list products from Db2"
echo -e "    GET  https://${ROUTE_HOST}/cart      — current cart contents"
echo -e "    GET  https://${ROUTE_HOST}/orders    — placed orders"
echo -e "    GET  https://${ROUTE_HOST}/health    — liveness check"
echo -e "    GET  https://${ROUTE_HOST}/ready     — readiness check (includes Db2 connectivity)"
echo ""
info "Check pod placement (Intel app + Power Db2):"
echo "    oc get pods -n $NAMESPACE -o wide"
echo ""
info "Check IBM Db2 on Power node:"
echo "    oc get pods -n $NAMESPACE -l app=db2 -o wide"
echo "    oc logs -f deploy/db2 -n $NAMESPACE"
echo ""
info "Tail app server logs:"
echo "    oc logs -f deploy/shop-cart -n $NAMESPACE"
echo ""
info "Rebuild app after code changes:"
echo "    oc start-build shop-cart-app --from-dir=./app/ --follow --wait -n $NAMESPACE"
echo "    oc rollout restart deployment/shop-cart -n $NAMESPACE"
echo ""
info "Cleanup:"
echo "    oc delete namespace $NAMESPACE"
echo ""