# Dockerfile
FROM scratch
LABEL io.talos.dev/extension="true"

# manifest.yaml + firmware file in build context
ADD manifest.yaml /
ADD arrowlake_vbt.bin /rootfs/usr/lib/firmware/i915/arrowlake_vbt.bin