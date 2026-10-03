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
def ck(r, what):
    if r != 0:
        s = n.nvmlErrorString; s.restype = ctypes.c_char_p
        return f"ERR({r}:{s(r).decode()})"
    return None
print("init", n.nvmlInit_v2(), flush=True)
h = ctypes.c_void_p(); n.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(h))
def s(fn):
    b = ctypes.create_string_buffer(96); r = getattr(n, fn)(h, b, 96); return ck(r, fn) or b.value.decode()
def u(fn, *a):
    v = U(); r = getattr(n, fn)(h, *a, ctypes.byref(v)); return ck(r, fn) or v.value
print("name", s("nvmlDeviceGetName"), "uuid", s("nvmlDeviceGetUUID"), "vbios", s("nvmlDeviceGetVbiosVersion"))
print("pcie gen cur/max(link)/max(gpu)", u("nvmlDeviceGetCurrPcieLinkGeneration"), u("nvmlDeviceGetMaxPcieLinkGeneration"), u("nvmlDeviceGetGpuMaxPcieLinkGeneration"),
      "width cur/max", u("nvmlDeviceGetCurrPcieLinkWidth"), u("nvmlDeviceGetMaxPcieLinkWidth"), "replays", u("nvmlDeviceGetPcieReplayCounter"))
nf = u("nvmlDeviceGetNumFans"); print("num_fans", nf, flush=True)
class FSI(ctypes.Structure): _fields_ = [("version", U), ("fan", U), ("speed", U)]
def rpm(i):
    st = FSI(ctypes.sizeof(FSI) | (1 << 24), i, 0)
    r = n.nvmlDeviceGetFanSpeedRPM(h, ctypes.byref(st)); return ck(r, "rpm") or st.speed
dur = int(sys.argv[1]) if len(sys.argv) > 1 else 180
t0 = time.time()
while time.time() - t0 <= dur:
    temp = U(); n.nvmlDeviceGetTemperature(h, 0, ctypes.byref(temp))
    pw = U(); n.nvmlDeviceGetPowerUsage(h, ctypes.byref(pw))
    ps = ctypes.c_int(); n.nvmlDeviceGetPerformanceState(h, ctypes.byref(ps))
    fans = []
    for i in range(nf if isinstance(nf, int) else 0):
        fans.append(f"fan{i}={u('nvmlDeviceGetFanSpeed_v2', i)}%/tgt={u('nvmlDeviceGetTargetFanSpeed', i)}%/rpm={rpm(i)}")
    print(f"t={time.time()-t0:5.0f}s temp={temp.value}C power={pw.value/1000:.1f}W P{ps.value} gen={u('nvmlDeviceGetCurrPcieLinkGeneration')} x{u('nvmlDeviceGetCurrPcieLinkWidth')} " + " ".join(fans), flush=True)
    time.sleep(15)
n.nvmlShutdown()  # release NVML cleanly before the process exits
