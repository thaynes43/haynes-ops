"""Checks for sitecustomize.py (the VRAM guard), run inside the immich-machine-learning image.

CPU only, so it is safe to run in the live pod; no GPU memory is touched:
  kubectl exec -i -n photos deploy/immich-machine-learning -c app -- sh -c \
    'mkdir -p /tmp/vg && cat > /tmp/vg/sitecustomize.py' < sitecustomize.py
  kubectl exec -i -n photos deploy/immich-machine-learning -c app -- sh -c \
    'cat > /tmp/vg/test.py && cd /tmp && PYTHONPATH=/tmp/vg:/usr/src CUDA_VISIBLE_DEVICES= python /tmp/vg/test.py; rm -rf /tmp/vg' \
    < test_sitecustomize.py
It must print "ALL OK". Run it after editing the guard, and after an Immich upgrade that changes
the onnxruntime version. That the guard really acts on the GPU is shown by the load test
(.agents/runbooks/immich-ml-backlog.md, "Load test").
"""

import os

os.environ["IMMICH_ML_CUDA_MEM_LIMIT_MB"] = "1536"

import numpy as np  # noqa: E402
import onnxruntime as ort  # noqa: E402  (the import the guard hooks)
from onnx import TensorProto, helper  # noqa: E402
from onnxruntime.capi import onnxruntime_inference_collection as oic  # noqa: E402

import sitecustomize as guard  # noqa: E402

# The hook patched onnxruntime on import.
assert oic.InferenceSession.__init__.__module__ == "sitecustomize", oic.InferenceSession.__init__.__module__
assert oic.Session.run.__module__ == "sitecustomize", oic.Session.run.__module__

LIMIT = str(1536 * 1048576)
IMMICH = [{"arena_extend_strategy": "kSameAsRequested", "device_id": "0"}, {"arena_extend_strategy": "kSameAsRequested"}]

# Immich's form (sessions/ort.py:127-171): provider names plus a parallel options list.
providers, options, device = guard.guard_provider_options(["CUDAExecutionProvider", "CPUExecutionProvider"], IMMICH)
assert device == "0"
assert options[0] == {"gpu_mem_limit": LIMIT, "arena_extend_strategy": "kSameAsRequested", "device_id": "0"}, options
assert options[1] == {"arena_extend_strategy": "kSameAsRequested"}, options
assert "gpu_mem_limit" not in IMMICH[0], "the caller's dict must not be mutated"

# A value the caller sets wins.
_, options, device = guard.guard_provider_options(["CUDAExecutionProvider"], [{"gpu_mem_limit": "5", "device_id": "1"}])
assert options[0]["gpu_mem_limit"] == "5" and device == "1"

# (name, options) tuples, and names without an options list.
providers, options, device = guard.guard_provider_options([("CUDAExecutionProvider", {"device_id": 0}), "CPUExecutionProvider"], None)
assert providers[0][1]["gpu_mem_limit"] == LIMIT and device == "0" and options is None
_, options, device = guard.guard_provider_options(["CUDAExecutionProvider", "CPUExecutionProvider"], None)
assert options == [{"gpu_mem_limit": LIMIT}, {}] and device == "0"

# CPU only: untouched, and no shrink.
_, options, device = guard.guard_provider_options(["CPUExecutionProvider"], [{"arena_extend_strategy": "kSameAsRequested"}])
assert device is None and options == [{"arena_extend_strategy": "kSameAsRequested"}]

# A real CPU session still builds and runs through the patched calls.
graph = helper.make_graph(
    [helper.make_node("Relu", ["x"], ["y"])],
    "g",
    [helper.make_tensor_value_info("x", TensorProto.FLOAT, [None, 3])],
    [helper.make_tensor_value_info("y", TensorProto.FLOAT, [None, 3])],
)
model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
model.ir_version = 8
session = ort.InferenceSession(model.SerializeToString(), providers=["CPUExecutionProvider"], provider_options=[{}])
assert getattr(session, "_vram_guard_device", None) is None
out = session.run(None, {"x": np.array([[-1, 0, 2]], dtype=np.float32)})[0]
assert out.tolist() == [[0, 0, 2]], out

# The duty-cycle pause: none at 1, three times the run at 0.25, capped at 30 s.
assert guard.pause_after(2.0, 1.0) == 0.0
assert abs(guard.pause_after(2.0, 0.25) - 6.0) < 1e-9
assert guard.pause_after(20.0, 0.25) == 30.0
os.environ["IMMICH_ML_GPU_DUTY_CYCLE"] = "0.25"
assert guard._duty_cycle() == 0.25
for bad in ("0", "-1", "2", "x"):
    os.environ["IMMICH_ML_GPU_DUTY_CYCLE"] = bad
    assert guard._duty_cycle() == 1.0, bad

print("ALL OK", ort.__version__)
