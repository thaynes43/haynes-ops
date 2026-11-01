# Private video-conditioned Foley worker (OP344)

This image adds an isolated, immutable ControlFoley inference environment for
short audio tracks conditioned on existing video. It does not alter ComfyUI's
Python packages, models, image workflow or voice services. The upstream source,
model and encoder revisions and SHA256 values are pinned in the Dockerfile and
provisioner. Original source/license notices remain in the image.

`provision.py --weights-dir /workspace/controlfoley-models` downloads and verifies
about 17.4 GiB of public inference assets, then atomically publishes a manifest.
Existing wrong or partial files require inspection rather than silent overwrite.
Runtime is offline and uses only these local files. The standalone adapter stages
encoders, generator and decoder sequentially on one externally selected 24 GiB
GPU; actual memory and audio quality require useful production verification.

The private service binds only Pod loopback port 8189. POST `/foley` accepts an
existing completed video-ledger ID, source-window start, duration 0.7–8 seconds,
text/negative prompts and a seed. It cannot accept arbitrary paths, URLs or shell
commands. GET `/foley/ID` and `/foley/ID/file` expose only the owned record/WAV.
No ingress or extra Kubernetes Service is introduced.

Deployment must pair this worker with the Wan API's `/workspace/compute.lock`
POSIX lease. Both take the lease before accepting work and hold it through child
cleanup, preventing video/audio overlap. The worker honors live Wan holds and
refuses unknown or malformed engine queues. It only evicts cached models from
an idle Comfy engine, then waits for cool GPU with less than2GiB residual memory (including CUDA context) before inference.
It runs one bounded subprocess, kills only that child group on failure/timeout,
and records incomplete jobs as failed on restart without replay. A sampled 88°C
job stop terminates only its own audio process; clocks, power and cooling remain
unchanged. Audio assets and histories persist under the existing Wan workspace.

The Docker test target runs CPU-only backend/control-plane tests. CI also imports
the real pinned inference dependencies offline on a read-only filesystem. These
checks establish source/import correctness, not GPU inference or listening quality.
No benchmarking, stress work, access expansion or hardware action is included.
The canonical story records source, tested, installed and usable states separately.
