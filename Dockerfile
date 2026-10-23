FROM jellyfin/jellyfin:12.3@sha256:2038fe038ab3876a80f6e7b7e0aef6607b032c7dc0c9b3ad3296d0b2eba3bbb5

ARG TARGETARCH
RUN set -eux; \
  arch="${TARGETARCH:-$(dpkg --print-architecture)}"; \
  apt-get update; \
  if [ "$arch" = "amd64" ]; then \
    # Intel OpenCL for real HW tone-mapping on Intel iGPU
    apt-get install -y --no-install-recommends \
      intel-opencl-icd ocl-icd-libopencl1 clinfo; \
  elif [ "$arch" = "arm64" ] || [ "$arch" = "arm" ] || [ "$arch" = "armhf" ]; then \
    # Software fallback; board-specific ARM acceleration needs a dedicated image.
    apt-get install -y --no-install-recommends \
      ocl-icd-libopencl1 clinfo; \
  else \
    # Safe default for any other arch
    apt-get install -y --no-install-recommends \
      ocl-icd-libopencl1 clinfo; \
  fi; \
  rm -rf /var/lib/apt/lists/*
