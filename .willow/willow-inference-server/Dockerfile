# Use the NVIDIA TensorRT base image
FROM nvcr.io/nvidia/tensorrt:23.08-py3

# Set environment variables
ENV DEBIAN_FRONTEND=noninteractive
ENV TMPDIR=/var/tmp
ENV CTRANSLATE2_ROOT=/opt/ctranslate2
ENV LD_LIBRARY_PATH=$LD_LIBRARY_PATH:$CTRANSLATE2_ROOT/lib

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git \
        curl \
        wget \
        build-essential \
        ca-certificates \
        python3-dev \
        python3-pip \
        python-is-python3 \
        ffmpeg \
        zstd \
        git-lfs \
        nginx \
        && rm -rf /var/lib/apt/lists/*

# Set the working directory
WORKDIR /app

# Clone the Willow Inference Server repository
RUN git clone https://github.com/toverainc/willow-inference-server.git .

# Copy the repository into the container
COPY . /app

# Install Python dependencies
RUN --mount=type=cache,target=/root/.cache \
    pip install -r requirements.txt

# Install Torch matching CUDA version
RUN --mount=type=cache,target=/root/.cache \
    pip install -U torch==2.1.0 torchvision==0.16.0 torchaudio==2.1.0

# Build and install ctranslate2
RUN git clone --recursive https://github.com/OpenNMT/CTranslate2.git && \
    cd CTranslate2 && \
    git checkout 2203ad5 && \
    mkdir build && cd build && \
    cmake -DCMAKE_INSTALL_PREFIX=${CTRANSLATE2_ROOT} \
          -DWITH_CUDA=ON -DWITH_CUDNN=ON -DWITH_MKL=ON -DWITH_DNNL=ON -DOPENMP_RUNTIME=COMP \
          -DCMAKE_BUILD_TYPE=Release .. && \
    make -j$(nproc) install && \
    cd ../python && \
    pip install -r install_requirements.txt && \
    python3 setup.py bdist_wheel --dist-dir $CTRANSLATE2_ROOT && \
    pip install $CTRANSLATE2_ROOT/*.whl && \
    rm $CTRANSLATE2_ROOT/*.whl && \
    cd /app && rm -rf CTranslate2

# Install auto-gptq
RUN --mount=type=cache,target=/root/.cache \
    pip install auto-gptq --extra-index-url https://huggingface.github.io/autogptq-index/whl/cu118/

# Remove 'sudo' commands from utils.sh
RUN sed -i 's/sudo //g' /app/willow-inference-server/utils.sh

# Remove 'docker' commands from utils.sh
RUN sed -i '/docker run/d' /app/willow-inference-server/utils.sh

# Download models during the build
RUN ./utils.sh download-models

# Generate self-signed TLS certificates (or replace with real certificates)
RUN ./utils.sh gen-cert localhost

# Copy nginx configuration
COPY nginx/nginx.conf /etc/nginx/nginx.conf

# Ensure entrypoint.sh is executable
RUN chmod +x /app/entrypoint.sh

# Expose necessary ports
EXPOSE 19000
EXPOSE 19001
EXPOSE 10000-10050/udp

# Start NGINX and the application
CMD ["./entrypoint.sh"]