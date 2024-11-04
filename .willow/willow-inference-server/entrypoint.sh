#!/bin/bash
set -e

# Navigate to the application directory
cd /app/willow-inference-server

# Check if models directory exists and is not empty
if [ -z "$(ls -A models 2>/dev/null)" ]; then
  echo "Models not found. Downloading models..."
  ./utils.sh download-models
else
  echo "Models already exist. Skipping download."
fi

# Start the application
./utils.sh run