"""Single-card, in-process llama.cpp and libmtmd adapter for the Wan lease."""
import argparse
import codecs
import ctypes as c
import json
import os
from pathlib import Path
import stat

import local_model_modes as modes
import local_model_sidecar as sidecar
import product
import product_local as local

MODULE_DIR = Path(__file__).resolve().parent
BYTE_LIMIT = local.MAX_TEXT_BYTES


def open_library(path=MODULE_DIR / "libstudio_local_backend.so"):
    lib = c.CDLL(str(path))
    pointer, text, size = c.c_void_p, c.c_char_p, c.c_size_t
    signatures = {
        "studio_model_open": ([text, text, c.c_uint32, c.c_int32], pointer),
        "studio_model_close": ([pointer], None),
        "studio_prepare": ([pointer, text, c.POINTER(text), c.POINTER(c.c_int), size, text, text,
                            c.POINTER(size), c.POINTER(size)], pointer),
        "studio_generate": ([pointer, pointer, c.c_int, size, c.POINTER(c.c_int), c.POINTER(c.c_bool),
                             c.POINTER(size)], pointer),
        "studio_request_free": ([pointer], None),
        "studio_cpu_check": ([text, text, text], c.c_bool),
    }
    for name, (args, result) in signatures.items():
        method = getattr(lib, name)
        method.argtypes = args
        method.restype = result
    return lib


class Prepared:
    def __init__(self, lib, handle):
        self.lib = lib
        self.handle = handle

    def close(self):
        if self.handle is not None:
            self.lib.studio_request_free(self.handle)
            self.handle = None

    def __del__(self):
        # The sidecar can reject the token count without calling infer().
        self.close()


def encode_json(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class UnsupportedProfile(ValueError):
    pass


def weight_paths(profile, root, pins):
    if (profile["model"] != pins["model"] or "s2v_gpu_uuid" in profile
            or profile["context_tokens"] > 8192 or profile["max_output_tokens"] > 2048
            or profile["unloaded_max_mib"] > local.MAX_UNLOADED_MIB):
        raise UnsupportedProfile("This image serves only the selected CR-20 profile")
    directory = root / pins["model"] / pins["revision"]
    with (directory / "manifest.json").open("rb") as manifest:
        if json.loads(manifest.read(4096)) != pins:
            raise ValueError("Weights manifest differs from the image pins")
    paths = [directory / item["name"] for item in pins["files"]]
    for path, pin in zip(paths, pins["files"], strict=True):
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size != pin["bytes"]:
            raise ValueError("Weight file differs from the provisioned sizes")
    return paths


class LlamaBackend:
    def __init__(self, profile, weights_root, pins, *, library=open_library):
        self.profile = profile
        self.weights_root = weights_root
        self.pins = pins
        self.library = library
        self.model = None

    def load(self):
        card = self.profile["gpu_uuid"]
        if sidecar.pin_environment_error(card):
            raise ValueError("Local-model GPU does not match the profile")
        weights, projector = weight_paths(self.profile, self.weights_root, self.pins)
        self.lib = self.library()
        self.model = self.lib.studio_model_open(os.fsencode(weights), os.fsencode(projector),
                                                self.profile["context_tokens"], local.MAX_VISUAL_TOKENS)
        if not self.model:
            raise RuntimeError("Local model could not initialize on its assigned GPU")

    def prepare(self, prompt, images, mode):
        if len(images) > local.MAX_IMAGES or not any(mode is value for value in modes.MODES.values()):
            raise ValueError("Invalid model inference envelope")
        frames = []
        for path in images:
            # OP441 verifies each staged digest and PNG envelope before this call.
            with path.open("rb") as source:
                frame = source.read(product.MAX_UPLOAD + 1)
            if not 0 < len(frame) <= product.MAX_UPLOAD:
                raise ValueError("Staged PNG exceeded its byte bound")
            frames.append(frame)
        schema = mode.get("schema")
        if mode is modes.MODES["frame_check_v1"]:
            schema = {**schema, "properties": {"frames": {
                **schema["properties"]["frames"], "minItems": len(frames), "maxItems": len(frames),
            }}}
        tool = mode.get("tool")
        data = (c.c_char_p * len(frames))(*frames)
        sizes = (c.c_int * len(frames))(*(len(frame) for frame in frames))
        total = c.c_size_t()
        visual = (c.c_size_t * len(frames))()
        handle = self.lib.studio_prepare(self.model, prompt.encode(), data, sizes, len(frames),
                                         encode_json(schema) if schema else b"", encode_json(tool) if tool else b"",
                                         c.byref(total), visual)
        if not handle:
            raise ValueError("Could not prepare the local-model input")
        return Prepared(self.lib, handle), total.value, list(visual)

    def infer(self, prepared, cap):
        output_tokens, complete, size = c.c_int(), c.c_bool(), c.c_size_t()
        try:
            data = self.lib.studio_generate(self.model, prepared.handle, cap, BYTE_LIMIT,
                                            c.byref(output_tokens), c.byref(complete), c.byref(size))
            if not data:
                raise RuntimeError("Local-model generation failed")
            if size.value > BYTE_LIMIT:
                # Do not copy an oversized response across the native boundary.
                return " " * (BYTE_LIMIT + 1), output_tokens.value, False
            # A token may end partway through a UTF-8 character. A capped
            # answer can discard that suffix, while invalid bytes elsewhere
            # still fail the sidecar's independent UTF-8 output check.
            decoder = codecs.getincrementaldecoder("utf-8")("surrogateescape")
            answer = decoder.decode(c.string_at(data, size.value), final=complete.value)
            return answer, output_tokens.value, complete.value
        finally:
            prepared.close()

    def unload(self):
        if self.model is not None:
            self.lib.studio_model_close(self.model)
            self.model = None


def listener_ready(root, pid=1):
    """Check this container's listener without connecting or doing GPU work."""
    path = local.socket_path(root)
    try:
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_mode & 0o077:
            return False
        with open("/proc/net/unix", encoding="utf-8") as sockets:
            for line in sockets:
                fields = line.rstrip("\n").split(maxsplit=7)
                if (len(fields) == 8 and fields[-1] == str(path) and fields[4] == "0005"
                        and int(fields[3], 16) & 0x10000):
                    inode = f"socket:[{fields[6]}]"
                    return any(owns_socket(fd, inode) for fd in Path(f"/proc/{pid}/fd").iterdir())
    except (OSError, ValueError):
        return False
    return False


def owns_socket(fd, inode):
    try:
        return os.readlink(fd) == inode
    except FileNotFoundError:
        return False


def startup(profile_path, weights_root):
    if profile_path.name != "local-model.json":
        return None, None, "profile_invalid"
    profile, reason = local.startup_profile(profile_path.parent)
    if reason:
        return None, None, reason
    try:
        pins = json.loads((MODULE_DIR / "weights.json").read_text())
        weight_paths(profile, weights_root, pins)
    except UnsupportedProfile:
        return profile, None, "profile_unsupported"
    except FileNotFoundError:
        return profile, None, "weights_missing"
    except (ValueError, KeyError, TypeError, RecursionError):
        return profile, None, "weights_mismatch"
    except OSError:
        return profile, None, "weights_unreadable"
    return profile, pins, None


def serve(workspace, profile_path, weights_root, *, pin_lock=None):
    profile, pins, reason = startup(profile_path, weights_root)
    sidecar.serve(workspace, profile, lambda p: LlamaBackend(p, weights_root, pins),
                  startup_error=reason, pin_lock=pin_lock)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("serve", "ready"):
        commands.add_parser(name).add_argument("--workspace", type=Path, default=Path("/workspace"))
    commands.choices["ready"].add_argument("--pid", type=int, default=1)
    serve_parser = commands.choices["serve"]
    serve_parser.add_argument("--profile", type=Path, default=Path("/config/video/local-model.json"))
    serve_parser.add_argument("--weights-root", type=Path, default=Path("/workspace/local-model-weights"))
    commands.add_parser("self-check")
    args = parser.parse_args()
    if args.command == "ready":
        return 0 if listener_ready(args.workspace, args.pid) else 1
    if args.command == "self-check":
        template = (MODULE_DIR / "Qwen3.5-4B.jinja").read_bytes()
        return 0 if open_library().studio_cpu_check(template, encode_json(modes.PLAN),
                                                    encode_json(modes.PLAN_TOOL)) else 1
    serve(args.workspace, args.profile, args.weights_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
