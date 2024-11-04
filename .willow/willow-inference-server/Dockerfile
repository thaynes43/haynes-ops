# Use a specific version of NVIDIA's base image with CUDA and Ubuntu 22.04
FROM nvidia/cuda:11.8.0-cudnn8-devel-ubuntu22.04

# Set environment variables
ENV DEBIAN_FRONTEND=noninteractive

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
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory
WORKDIR /app

# Clone the Willow Inference Server repository
RUN git clone https://github.com/toverainc/willow-inference-server.git /app/willow-inference-server

# Set the working directory to the cloned repository
WORKDIR /app/willow-inference-server

# Install Python dependencies
RUN pip3 install --no-cache-dir -r requirements.txt

# Generate self-signed TLS certificates (you may replace this with real certificates)
RUN ./utils.sh gen-cert localhost

# Expose necessary ports
EXPOSE 19000 19001
EXPOSE 10000-10050/udp

# Copy the entrypoint script into the container
COPY entrypoint.sh /app/willow-inference-server/entrypoint.sh
RUN chmod +x /app/willow-inference-server/entrypoint.sh

# Set the entrypoint to run the entrypoint script
ENTRYPOINT ["./entrypoint.sh"]