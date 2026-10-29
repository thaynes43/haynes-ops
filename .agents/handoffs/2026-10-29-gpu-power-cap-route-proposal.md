# Proposal: a bounded GPU power-cap Job route for capped comparisons

Written 2026-10-28 by session `haynes-ops-1027-150450` and moved here on 2026-10-29. Context:
[2026-10-29-gpu-pcie-relayout.md](2026-10-29-gpu-pcie-relayout.md) and
[OP#221](https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/221).
Tracked as [OP#233](https://work.haynesnetwork.com/projects/haynes-engineering/work_packages/233).

**This is a proposal only.** Nothing has been applied: no policy, test, grant, or GPU or host
probe. Tom has to rule on it first. The sources are haynes-ops at a767e4a4, the Job records in
`ai`, and `kubectl auth whoami`.

## The gap
`scripts/gpu-soak` applies a power cap through `SOAK_POWER_LIMIT_W`, which runs
`nvidia-smi -pm 1` and `-pl` as root with SYS_ADMIN. Kyverno already admits that for
`egpu-test-*` Jobs (`pss-baseline-egpu-test-capabilities`), but dev-env-agent-guard refuses any
added capability outside the PSS baseline. A v2 session therefore cannot run capped
comparisons, such as Card C at 300 W or a re-apply of Card B's 250 W after a reboot.

## Route
A short **setter** Job sets the limit. The load stays in the existing unprivileged soak Job.
Torch, CUDA and the load never run with SYS_ADMIN. This generalises `render-job.sh --cap-only`
and `kubernetes/main/apps/ai/llama-server/app/card-b-power-cap.sh` to a fixed table.

### Requester binding
- `request.userInfo.username == 'system:serviceaccount:dev-agents:dev-env-agent'`.
- `request.userInfo.extra['authentication.kubernetes.io/pod-name']` equals the executing
  session's pod name. A v2 session pod is named after its session, and the name survives a
  resume; the pod UID does not. Name the session in the PR that adds the exception.

### Namespace and name
Namespace `ai`. The Job name must match `^egpu-test-cap-(cardb|cardc)-[0-9]{8}-[a-z0-9]{1,6}$`,
which falls inside the existing Kyverno exception.

### Card table (from records; re-verify at run time)
| Card | UUID | Node | Allowed `POWER_W` |
|---|---|---|---|
| B (optional row) | `GPU-18bf6eab-c76a-26ba-74c8-76093b705b8b` | talosw04, with only the toleration `haynesops.com/gpu-test=true:NoSchedule` | exactly 250. Lowers only: a lower existing limit stays, and B is never raised. |
| C | `GPU-d8a856f1-f955-f683-bc24-654561496774` | talosw01, no tolerations | 300, or a restore to the card's `power.default_limit` read back (350) |
| A | `GPU-6ff9702a-1b2c-6094-5a85-00aa1924c55a` | talosw01 | not included |

The setter re-checks UUID and node at run time and fails closed. A slot move keeps both rack
cards in VM 103, but treat the C row as unverified until it is re-checked after OP#221.

### Image
- Pin `ghcr.io/thaynes43/comfyui:0.39.0-2@sha256:991867f5dafc1eddc9ed70b307d8bf1d2d91c50f53a428a6315e8fd7dbe1a61b`
  (the imageID of the r5 and r6 pods). `render-job.sh` copies it from `sts/comfyui`; refuse to
  render if that has moved.
- **Signatures are not being verified.** Kyverno `verify-thaynes43-images` covers
  `ghcr.io/thaynes43/comfyui*` with keyless signing. It runs with `failureAction: Audit`,
  `failurePolicy: Ignore` and `background: false`. On 2026-10-28 14:29:45Z it warned that this
  digest fails verification: "x509: certificate signed by unknown authority … Check your TUF
  root". The digest pin is the only control actually enforced. Fixing the TUF root, or ruling
  on it, belongs with this story.

### Fixed command and bounds
- One container, `power-cap`, with `command: ["/bin/sh","-c"]`. `args[0]` holds the exact bytes
  of one script, compared to a CEL variable `powerCapScript`.
  - CEL has no hash function, so the check is an exact string match.
  - A CI test keeps the variable and the repo script byte-identical.
- The script:
  - accepts only (UUID, node, watts) rows from its built-in table and checks `POD_NODE_NAME`;
  - refuses if a compute process is active or the process list can't be read;
  - runs `nvidia-smi -i <UUID> -pm 1` and `-pl <W>`, then reads back both the configured and
    the enforced limit;
  - fails closed on any identity, command or read-back error.
- Environment, exactly: `NVIDIA_VISIBLE_DEVICES=<UUID>`, `NVIDIA_DRIVER_CAPABILITIES=utility`,
  `POWER_W`, and `POD_NODE_NAME` (from a fieldRef).
- The `power-cap` container alone gets SYS_ADMIN:
  - `runAsUser: 0`, `runAsNonRoot: false`;
  - capabilities `drop: [ALL]`, `add: [SYS_ADMIN]`;
  - `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem: true`;
  - `automountServiceAccountToken: false`;
  - no initContainers, other containers, volumes, hostPath, or host network, PID or IPC;
  - `runtimeClassName: nvidia`;
  - resources: requests 50m / 32Mi, limits 100m / 128Mi.
- Job: `backoffLimit: 0`, `activeDeadlineSeconds <= 180`, `ttlSecondsAfterFinished <= 86400`.
  Card B carries only its one Equal toleration; Card C carries none.
- Anything else still fails `podViolations`. `baselineCapabilities` is unchanged, and nothing
  grants a blanket capability.

### Duration
- Add the exception as a dated block in one PR. Remove it in a follow-up PR once the comparison
  is recorded, or after 7 days, whichever comes first. CEL has no clock, so GitOps enforces the
  expiry.
- One comparison is three Jobs, each on Tom's go:
  1. cap C to 300 W and read it back;
  2. run the unprivileged 75 s soak;
  3. restore C to its default and read it back.
- A failed restore must be reported. The limit also resets when VM 103 or the driver restarts.
  Grafana `nvidia_smi_power_limit_watts` shows the limit in force.

## Decisions for Tom
- Is the 300 W comparison still wanted after OP#221? C may stop throttling in its new slot.
- Does the Card B row go in? It gives a way to re-apply 250 W after a talosw04 reboot until
  OP#199's startup cap is approved. It can never raise B.
