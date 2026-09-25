#!/usr/bin/env python3
"""GPU soak for one passed-through card: idle->burst cycles, then sustained heat (#3052).

Every record is one CSV line on stdout, so Loki keeps the whole run after the Job is gone:

  S,<ts>,<phase>,<temp C>,<fan %>,<power W>,<sm MHz>,<mem MHz>,<pcie gen>,<pcie width>,<reasons>,<util %>,<vram MiB>
  B,<ts>,<phase>,<t since burst start s>,<TFLOPS over the last window>
  E,<ts>,<event>,<detail>
  R,<phase>,<key=value ...>        (summary, printed at the end)

SOAK_PLAN is a comma list of <name>:<cycles>:<idle s>:<burst s>. The default runs the failure
trigger seen on 2026-09-23 (a render starting from idle, when the link retrains from Gen1),
then 20 min of sustained load, then the trigger again from a hot idle, then a cool-down.
"""
import os
import statistics
import subprocess
import sys
import threading
import time

PLAN = os.environ.get("SOAK_PLAN", "trigger:4:240:90,sustain:1:0:1200,hottrigger:2:240:90,cool:1:300:0")
SAMPLE_MS = int(os.environ.get("SOAK_SAMPLE_MS", "2000"))
MATRIX = int(os.environ.get("SOAK_MATRIX", "8192"))
WINDOW_S = float(os.environ.get("SOAK_WINDOW_S", "5"))

QUERY = ("temperature.gpu,fan.speed,power.draw,clocks.sm,clocks.mem,pcie.link.gen.current,"
         "pcie.link.width.current,clocks_event_reasons.active,utilization.gpu,memory.used")
# clocks_event_reasons bits worth counting (nvml.h)
REASONS = {"sw_power": 0x4, "hw_slowdown": 0x8, "sw_thermal": 0x20, "hw_thermal": 0x40, "hw_power_brake": 0x80}

phase = "init"
samples = []            # (phase, temp, sm, reasons, power, fan)
bursts = {}             # phase -> [(t, tflops)]
lock = threading.Lock()
stop = threading.Event()


def ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def emit(*fields):
    print(",".join(str(f) for f in fields), flush=True)


def num(v):
    try:
        return float(v)
    except ValueError:
        return None


def sampler():
    while not stop.is_set():
        proc = subprocess.Popen(["nvidia-smi", f"--query-gpu={QUERY}", "--format=csv,noheader,nounits",
                                 f"-lms={SAMPLE_MS}"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in proc.stdout:
            if stop.is_set():
                break
            f = [x.strip() for x in line.split(",")]
            if len(f) != 10:
                emit("E", ts(), "SMI_LINE", line.strip().replace(",", ";"))
                continue
            with lock:
                p = phase
            emit("S", ts(), p, *f)
            try:
                reasons = int(f[7], 16)
            except ValueError:
                reasons = None
            with lock:
                samples.append((p, num(f[0]), num(f[3]), reasons, num(f[2]), num(f[1])))
        proc.kill()
        if not stop.is_set():
            emit("E", ts(), "SMI_EXIT", f"rc={proc.wait()}")
            time.sleep(5)


def set_phase(p):
    global phase
    with lock:
        phase = p


def main():
    threading.Thread(target=sampler, daemon=True).start()
    emit("E", ts(), "START", f"plan={PLAN};matrix={MATRIX};visible={os.environ.get('NVIDIA_VISIBLE_DEVICES', '?')}")
    import torch  # imported after the sampler starts so a CUDA init failure is still sampled

    dev = torch.device("cuda:0")
    emit("E", ts(), "DEVICE", torch.cuda.get_device_name(dev).replace(",", ";"))
    a = torch.randn(MATRIX, MATRIX, device=dev, dtype=torch.float16)
    b = torch.randn(MATRIX, MATRIX, device=dev, dtype=torch.float16)
    c = torch.empty_like(a)
    torch.cuda.synchronize()
    flops = 2 * MATRIX ** 3

    def burst(p, seconds):
        set_phase(p)
        t0 = time.time()
        torch.mm(a, b, out=c)
        torch.cuda.synchronize()
        emit("E", ts(), "FIRST_KERNEL", f"{p};{time.time() - t0:.3f}s")
        win_t, win_n = time.time(), 0
        while time.time() - t0 < seconds:
            for _ in range(10):
                torch.mm(a, b, out=c)
            torch.cuda.synchronize()
            win_n += 10
            now = time.time()
            if now - win_t >= WINDOW_S:
                tf = win_n * flops / (now - win_t) / 1e12
                emit("B", ts(), p, f"{now - t0:.0f}", f"{tf:.1f}")
                bursts.setdefault(p, []).append((now - t0, tf))
                win_t, win_n = now, 0

    failed = None
    try:
        for step in PLAN.split(","):
            name, cycles, idle_s, burst_s = step.split(":")
            for i in range(1, int(cycles) + 1):
                if int(idle_s):
                    set_phase(f"{name}{i}-idle")
                    time.sleep(int(idle_s))
                if int(burst_s):
                    burst(f"{name}{i}-burst", int(burst_s))
    except RuntimeError as e:   # CUDA errors surface as RuntimeError (Xid 79 = launch failure / device lost)
        failed = str(e).splitlines()[0][:300]
        emit("E", ts(), "CUDA_ERROR", failed.replace(",", ";"))
        set_phase("after-error")
        time.sleep(60)          # keep sampling: does nvidia-smi still see the card?

    stop.set()
    time.sleep(SAMPLE_MS / 1000 + 1)
    summarize()
    emit("E", ts(), "END", "FAILED" if failed else "OK")
    sys.exit(2 if failed else 0)


def summarize():
    with lock:
        rows = list(samples)
    for p in dict.fromkeys(r[0] for r in rows):
        rs = [r for r in rows if r[0] == p]
        temps = [r[1] for r in rs if r[1] is not None]
        sms = [r[2] for r in rs if r[2] is not None]
        pw = [r[4] for r in rs if r[4] is not None]
        fans = [r[5] for r in rs if r[5] is not None]
        rsn = [r[3] for r in rs if r[3] is not None]
        kv = [f"n={len(rs)}"]
        if temps:
            kv += [f"temp_max={max(temps):.0f}", f"temp_end={temps[-1]:.0f}"]
        if sms:
            kv += [f"sm_min={min(sms):.0f}", f"sm_med={statistics.median(sms):.0f}"]
        if pw:
            kv += [f"power_max={max(pw):.0f}"]
        if fans:
            kv += [f"fan_max={max(fans):.0f}"]
        if rsn:
            kv += [f"{k}_pct={100 * sum(1 for x in rsn if x & bit) / len(rsn):.0f}" for k, bit in REASONS.items()]
        tf = bursts.get(p)
        if tf:
            vals = [v for _, v in tf]
            kv += [f"tflops_first={vals[0]:.1f}", f"tflops_med={statistics.median(vals):.1f}",
                   f"tflops_last={vals[-1]:.1f}"]
        emit("R", p, " ".join(kv))


if __name__ == "__main__":
    main()
