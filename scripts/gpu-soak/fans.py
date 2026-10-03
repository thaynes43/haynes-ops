#!/usr/bin/env python3
"""Per-fan NVML sampler for the eGPU test node (.agents/runbooks/egpu-test-node.md).

nvidia-smi only reports one aggregate "Fan Speed". A card with a broken fan needs each fan
channel's duty (%), target (%) and tachometer RPM, which NVML exposes per channel. Read-only:
no root, no capabilities, no load. Usage: python fans.py [seconds=180]; one line per 15 s.
Zero-RPM cards stop their fans at idle (card A, 2026-10-03: 0 rpm on both channels against a
30 % target at 48 C), so judge the fans during a load stage, not at idle. Run it as its own
Job next to a soak Job, same placement (see the runbook).
"""
import ctypes
import sys
import time

n = ctypes.CDLL("libnvidia-ml.so.1")
U = ctypes.c_uint
n.nvmlErrorString.restype = ctypes.c_char_p


def ck(r):
    """None on success, else a printable ERR(...) so a failed read never looks like a value."""
    return None if r == 0 else f"ERR({r}:{n.nvmlErrorString(r).decode()})"


r = n.nvmlInit_v2()
print("init", r, flush=True)
if r:
    sys.exit(f"nvmlInit failed: {ck(r)}")
h = ctypes.c_void_p()
r = n.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(h))
if r:
    sys.exit(f"no GPU handle: {ck(r)}")


def s(fn):
    b = ctypes.create_string_buffer(96)
    return ck(getattr(n, fn)(h, b, 96)) or b.value.decode()


def u(fn, *a, ctype=U):
    v = ctype()
    return ck(getattr(n, fn)(h, *a, ctypes.byref(v))) or v.value


class FSI(ctypes.Structure):
    _fields_ = [("version", U), ("fan", U), ("speed", U)]


def rpm(i):
    st = FSI(ctypes.sizeof(FSI) | (1 << 24), i, 0)  # nvmlFanSpeedInfo_v1
    return ck(n.nvmlDeviceGetFanSpeedRPM(h, ctypes.byref(st))) or st.speed


print("name", s("nvmlDeviceGetName"), "uuid", s("nvmlDeviceGetUUID"), "vbios", s("nvmlDeviceGetVbiosVersion"))
print("pcie gen cur/max(link)/max(gpu)", u("nvmlDeviceGetCurrPcieLinkGeneration"),
      u("nvmlDeviceGetMaxPcieLinkGeneration"), u("nvmlDeviceGetGpuMaxPcieLinkGeneration"),
      "width cur/max", u("nvmlDeviceGetCurrPcieLinkWidth"), u("nvmlDeviceGetMaxPcieLinkWidth"),
      "replays", u("nvmlDeviceGetPcieReplayCounter"))
nf = u("nvmlDeviceGetNumFans")
print("num_fans", nf, flush=True)
dur = int(sys.argv[1]) if len(sys.argv) > 1 else 180
t0 = time.time()
while time.time() - t0 <= dur:
    temp = u("nvmlDeviceGetTemperature", 0)  # 0 = NVML_TEMPERATURE_GPU
    pw = u("nvmlDeviceGetPowerUsage")
    pw = f"{pw / 1000:.1f}" if isinstance(pw, int) else pw
    ps = u("nvmlDeviceGetPerformanceState", ctype=ctypes.c_int)
    fans = [f"fan{i}={u('nvmlDeviceGetFanSpeed_v2', i)}%/tgt={u('nvmlDeviceGetTargetFanSpeed', i)}%/rpm={rpm(i)}"
            for i in range(nf if isinstance(nf, int) else 0)]
    print(f"t={time.time() - t0:5.0f}s temp={temp}C power={pw}W P{ps} "
          f"gen={u('nvmlDeviceGetCurrPcieLinkGeneration')} x{u('nvmlDeviceGetCurrPcieLinkWidth')} " + " ".join(fans),
          flush=True)
    time.sleep(15)
n.nvmlShutdown()  # release NVML cleanly before the process exits
