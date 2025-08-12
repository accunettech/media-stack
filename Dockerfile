FROM jellyfin/jellyfin:latest

# OpenCL ICD + loader + clinfo for verification
RUN apt-get update && apt-get install -y --no-install-recommends \
    intel-opencl-icd ocl-icd-libopencl1 clinfo \
 && rm -rf /var/lib/apt/lists/*
