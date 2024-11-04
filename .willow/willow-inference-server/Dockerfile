# Use a specific version of NVIDIA's base image with CUDA and Ubuntu 22.04
FROM nvidia/cuda:11.8.0-cudnn8-devel-ubuntu22.04

# Set environment variables
ENV DEBIAN_FRONTEND=noninteractive
ENV TMPDIR=/var/tmp

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    git \
    curl \
    wget \
    build-essential \
    ca-certificates \
    python3 \
    python3-pip \
    python3-dev \
    python-is-python3 \
    ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Set the working directory
WORKDIR /app

# Clone the Willow Inference Server repository
RUN git clone https://github.com/toverainc/willow-inference-server.git /app/willow-inference-server

# Remove 'sudo' commands from utils.sh
RUN sed -i 's/sudo //g' /app/willow-inference-server/utils.sh

# Remove 'docker' commands from utils.sh
RUN sed -i '/docker run/d' /app/willow-inference-server/utils.sh

# Set the working directory to the cloned repository
WORKDIR /app/willow-inference-server

# Install Python dependencies and clean up
RUN mkdir -p /var/tmp && \
    pip3 install --no-cache-dir -r requirements.txt && \
    rm -rf /root/.cache/pip && \
    rm -rf /var/tmp/*

# Generate self-signed TLS certificates (you may replace this with real certificates)
RUN ./utils.sh gen-cert localhost

# Download the models during the build
RUN ./utils.sh whisper-model tovera/wis-whisper-tiny && \
    ./utils.sh whisper-model tovera/wis-whisper-base && \
    ./utils.sh whisper-model tovera/wis-whisper-small && \
    ./utils.sh whisper-model tovera/wis-whisper-medium && \
    ./utils.sh whisper-model tovera/wis-whisper-large-v2 && \
    ./utils.sh t5-model && \
    ./utils.sh sv-model

# Expose necessary ports
EXPOSE 19001
EXPOSE 10000-10050/udp

# Set environment variables
ENV LISTEN_PORT=19001
ENV FORWARDED_ALLOW_IPS="*"
ENV LOG_LEVEL=info

# Start the application
CMD gunicorn main:app --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:19001 \
    --graceful-timeout 10 \
    --forwarded-allow-ips "*" \
    --log-level info -t 0