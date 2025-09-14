#!/bin/bash

# Check if the first command line argument ($1) is provided
if [ -n "$1" ]; then
  # If an argument exists, use it as the namespace
  NAMESPACE="$1"
  echo "Watching Kustomizations in namespace: $NAMESPACE"
  watch -n 1 --no-wrap flux get kustomizations -n "$NAMESPACE"
else
  # If no argument is provided, use --all-namespaces
  echo "Watching Kustomizations in --all-namespaces"
  #watch -n 1 --no-wrap flux get kustomizations --all-namespaces
  watch kubectl get kustomizations --all-namespaces
fi
