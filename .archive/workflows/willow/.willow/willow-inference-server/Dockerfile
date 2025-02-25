# Use the NVIDIA TensorRT base image
FROM nvcr.io/nvidia/tensorrt:25.02-py3

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
        cmake \
        libprotobuf-dev \
        protobuf-compiler \
        zlib1g-dev \
        libunwind-dev \
        libssl-dev \
        libopenblas-dev \
        libbz2-dev \
        liblzma-dev \
        && rm -rf /var/lib/apt/lists/*

# Install Intel MKL
ENV ONEAPI_VERSION=2023.0.0
RUN wget -q https://apt.repos.intel.com/intel-gpg-keys/GPG-PUB-KEY-INTEL-SW-PRODUCTS.PUB && \
    apt-key add GPG-PUB-KEY-INTEL-SW-PRODUCTS.PUB && \
    rm GPG-PUB-KEY-INTEL-SW-PRODUCTS.PUB && \
    echo "deb https://apt.repos.intel.com/oneapi all main" > /etc/apt/sources.list.d/oneAPI.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        intel-oneapi-mkl-devel-$ONEAPI_VERSION \
        && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install cmake via pip to get a newer version
RUN pip install cmake==3.22.*

# Build and install oneDNN
ENV ONEDNN_VERSION=3.1.1
RUN wget -q https://github.com/oneapi-src/oneDNN/archive/refs/tags/v${ONEDNN_VERSION}.tar.gz && \
    tar xf v${ONEDNN_VERSION}.tar.gz && \
    rm v${ONEDNN_VERSION}.tar.gz && \
    cd oneDNN-${ONEDNN_VERSION} && \
    mkdir build && cd build && \
    cmake -DCMAKE_BUILD_TYPE=Release \
          -DONEDNN_LIBRARY_TYPE=STATIC \
          -DONEDNN_BUILD_EXAMPLES=OFF \
          -DONEDNN_BUILD_TESTS=OFF \
          -DONEDNN_ENABLE_WORKLOAD=INFERENCE \
          -DONEDNN_ENABLE_PRIMITIVE="CONVOLUTION;REORDER" \
          -DONEDNN_BUILD_GRAPH=OFF .. && \
    make -j$(nproc) install && \
    cd ../.. && \
    rm -rf oneDNN-${ONEDNN_VERSION}

# Set build environment variables
ARG CXX_FLAGS
ENV CXX_FLAGS=${CXX_FLAGS:-"-msse4.1"}
ARG CUDA_NVCC_FLAGS
ENV CUDA_NVCC_FLAGS=${CUDA_NVCC_FLAGS:-"-Xfatbin=-compress-all"}
ARG CUDA_ARCH_LIST
ENV CUDA_ARCH_LIST=${TORCH_CUDA_ARCH_LIST:-"Common"}

# Build and install ctranslate2
RUN git clone --recursive https://github.com/OpenNMT/CTranslate2.git && \
    cd CTranslate2 && \
    git checkout 2203ad5 && \
    mkdir build && cd build && \
    cmake -DCMAKE_INSTALL_PREFIX=${CTRANSLATE2_ROOT} \
          -DWITH_CUDA=ON \
          -DWITH_CUDNN=ON \
          -DWITH_MKL=ON \
          -DWITH_DNNL=ON \
          -DOPENMP_RUNTIME=COMP \
          -DCMAKE_BUILD_TYPE=Release \
          -DCMAKE_CXX_FLAGS="${CXX_FLAGS}" \
          -DCUDA_NVCC_FLAGS="${CUDA_NVCC_FLAGS}" \
          -DCUDA_ARCH_LIST="${CUDA_ARCH_LIST}" .. && \
    make -j$(nproc) install && \
    cd ../python && \
    pip install -r install_requirements.txt && \
    python3 setup.py bdist_wheel --dist-dir $CTRANSLATE2_ROOT && \
    pip install $CTRANSLATE2_ROOT/*.whl && \
    rm $CTRANSLATE2_ROOT/*.whl && \
    cd /app && rm -rf CTranslate2

# Set the working directory
WORKDIR /app

# Clone the Willow Inference Server repository
RUN git clone https://github.com/toverainc/willow-inference-server.git .

# Install Python dependencies
RUN --mount=type=cache,target=/root/.cache \
    pip install -r requirements.txt

# Install Torch matching CUDA version
RUN --mount=type=cache,target=/root/.cache \
    pip install -U torch==2.1.0 torchvision==0.16.0 torchaudio==2.1.0

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

# Ensure entrypoint.sh is executable
RUN chmod +x /app/entrypoint.sh

# Expose necessary ports
EXPOSE 19000
EXPOSE 19001
EXPOSE 10000-10050/udp

# Start NGINX and the application
CMD ["/app/entrypoint.sh"]