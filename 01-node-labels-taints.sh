#!/bin/bash
# =============================================================================
# Script: 01-node-labels-taints.sh
# Purpose: Label and optionally taint the Intel (x86_64) and Power (ppc64le)
#          worker nodes in the Hosted Control Plane OCP cluster so that
#          workloads can be pinned to the correct architecture.
#
#   Intel node  → workload-type=appserver  (Shopping Cart app)
#   Power node  → workload-type=database   (IBM Db2 Community Edition)
#
# Usage:
#   1. Log in to your HCP guest cluster:
#        oc login <api-url> --token=<token>
#   2. Find your worker node names:
#        oc get nodes -o wide
#   3. Set the variables below and run this script.
# =============================================================================

set -euo pipefail

# ---- CONFIGURE THESE --------------------------------------------------------
INTEL_NODE=""   # e.g. worker-intel-0  (x86_64 / amd64)
POWER_NODE=""   # e.g. worker-power-0  (ppc64le / IBM Power)
# -----------------------------------------------------------------------------

if [[ -z "$INTEL_NODE" || -z "$POWER_NODE" ]]; then
  echo "ERROR: Set INTEL_NODE and POWER_NODE variables before running."
  exit 1
fi

echo "==> Labeling Intel (x86_64) node: $INTEL_NODE"
oc label node "$INTEL_NODE" \
  node-role.kubernetes.io/intel-worker="" \
  workload-type=appserver \
  --overwrite

echo "==> Labeling Power (ppc64le) node: $POWER_NODE"
oc label node "$POWER_NODE" \
  node-role.kubernetes.io/power-worker="" \
  workload-type=database \
  --overwrite

# Optional: taint nodes so ONLY explicitly tolerating pods land on them.
# Uncomment the lines below if you want strict placement.
#
# echo "==> Tainting Intel node (appserver-only)"
# oc adm taint node "$INTEL_NODE" dedicated=appserver:NoSchedule --overwrite
#
# echo "==> Tainting Power node (database-only)"
# oc adm taint node "$POWER_NODE" dedicated=database:NoSchedule --overwrite

echo ""
echo "==> Node labels applied. Verify with:"
echo "    oc get nodes --show-labels"
echo "    oc describe node $INTEL_NODE | grep -A5 Labels"
echo "    oc describe node $POWER_NODE | grep -A5 Labels"