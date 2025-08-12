FROM jellyfin/jellyfin:latest

ARG TARGETARCH
RUN set -eux; \
  arch="${TARGETARCH:-$(dpkg --print-architecture)}"; \
  apt-get update; \
  if [ "$arch" = "amd64" ]; then \
    # Intel OpenCL for real HW tone-mapping on Intel iGPU
    apt-get install -y --no-install-recommends \
      intel-opencl-icd ocl-icd-libopencl1 clinfo; \
  elif [ "$arch" = "arm64" ] || [ "$arch" = "arm" ] || [ "$arch" = "armhf" ]; then \
    # Generic CPU OpenCL so ffmpeg loads the OpenCL filter without breaking
    apt-get install -y --no-install-recommends \
      ocl-icd-libopencl1 pocl-opencl-icd clinfo; \
  else \
    # Safe default for any other arch
    apt-get install -y --no-install-recommends \
      ocl-icd-libopencl1 clinfo; \
  fi; \
  rm -rf /var/lib/apt/lists/*
