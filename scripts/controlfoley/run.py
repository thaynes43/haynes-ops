#!/usr/bin/env python3
"""Offline single-GPU TV2A CLI with sequential encoder/generator/decoder residency.

Modified inference adapter based on Xiaomi ControlFoley inference_utils.py and
feature_extractor.py at SOURCE_REVISION (Apache-2.0).
Copyright (C) 2026 MiLM Plus, Xiaomi Corporation.
Original source and full license notices are preserved in /opt/controlfoley.
Changes: TV2A-only local constructors, staged residency, small video batches,
strict input/output validation, exact-length WAV and reproducibility provenance.
"""
import argparse
import gc
import json
import math
import os
from pathlib import Path
import sys
import time

from provision import SOURCE_REVISION, sha256, verify

SAMPLE_RATE = 44100
STEPS = 25


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--prompt", default="")
    parser.add_argument("--negative-prompt", default="")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--duration", type=float, default=8.0)
    parser.add_argument("--weights-dir", type=Path, default=Path("/workspace/controlfoley-models"))
    parser.add_argument("--check-imports", action="store_true")
    args = parser.parse_args(argv)
    if args.check_imports:
        return args
    if not math.isfinite(args.duration) or not 0.7 <= args.duration <= 8.0:
        parser.error("duration must be finite and between 0.7 and 8 seconds")
    if not 0 <= args.seed <= 0xFFFFFFFF:
        parser.error("seed must be between 0 and 4294967295")
    for name in ("video", "output", "weights_dir"):
        path = getattr(args, name)
        if path is None or not path.is_absolute():
            parser.error(f"{name} must be an absolute path")
    if not args.video.is_file():
        parser.error("video must be an existing file")
    if args.output.suffix.lower() != ".wav":
        parser.error("output must end in .wav")
    if args.output.exists() or args.output.with_suffix(args.output.suffix + ".json").exists():
        parser.error("output or provenance already exists; refusing overwrite")
    return args


def imports():
    source = Path(os.environ.get("CONTROLFOLEY_SOURCE_DIR", "/opt/controlfoley"))
    sys.path.insert(0, str(source))
    import av
    import numpy as np
    import torch
    import open_clip
    import soundfile as sf
    from torchvision.transforms import v2
    from controlfoley.audio_model import create_audio_generation_model
    from controlfoley.temporal_config import TemporalConfiguration
    from lib.synchformer import Synchformer
    from lib.cav_mae_st.core.models import CAVMAEST
    from lib.autoencoder.vae import get_my_vae
    from lib.bigvgan_v2.bigvgan import BigVGAN, load_hparams_from_json
    from lib.flow_matching import FlowMatching
    return locals()


def video_frames(video, duration, modules):
    """Decode only the requested prefix; cap decoder threading and resized RAM.

    Uses upstream's 8/4/25fps grids, bicubic resize and channel normalization.
    Frames are sampled by timestamp, never using the source video's audio.
    """
    av, torch, v2 = (modules[k] for k in ("av", "torch", "v2"))
    rates = (8, 4, 25)
    counts = [int(duration * rate) for rate in rates]
    frames = [[], [], []]
    with av.open(str(video)) as container:
        if not container.streams.video:
            raise ValueError("Input has no video stream")
        stream = container.streams.video[0]
        stream.codec_context.thread_count = 2
        for frame in container.decode(stream):
            if frame.time is None:
                raise ValueError("Video requires timestamps")
            if frame.time > duration:
                break
            for index, rate in enumerate(rates):
                while len(frames[index]) < counts[index] and frame.time + 1e-7 >= len(frames[index]) / rate:
                    size = 384 if index == 0 else 224
                    # Resize before storing so high-resolution source cannot fill RAM.
                    array = frame.reformat(width=size, height=size, format="rgb24", interpolation="BICUBIC").to_ndarray()
                    frames[index].append(torch.from_numpy(array).permute(2, 0, 1))
            if all(len(group) == count for group, count in zip(frames, counts)):
                break
    if any(len(group) != count for group, count in zip(frames, counts)):
        raise ValueError("Video is shorter than the requested generation duration")
    tensors = [torch.stack(group).float().div_(255) for group in frames]
    tensors[0] = v2.Normalize([0.48145466, 0.4578275, 0.40821073], [0.26862954, 0.26130258, 0.27577711])(tensors[0])
    tensors[1] = v2.Normalize([0.4850, 0.4560, 0.4060], [0.2290, 0.2240, 0.2250])(tensors[1])
    tensors[2] = v2.Normalize([0.5] * 3, [0.5] * 3)(tensors[2])
    return tensors


def release(torch):
    gc.collect()
    torch.cuda.empty_cache()


def generate(args, modules):
    torch, open_clip = modules["torch"], modules["open_clip"]
    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise RuntimeError("Exactly one externally selected CUDA GPU must be visible")
    torch.set_num_threads(min(2, int(os.environ.get("OMP_NUM_THREADS", "2"))))
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    torch.manual_seed(args.seed)
    torch.cuda.manual_seed_all(args.seed)
    torch.cuda.reset_peak_memory_stats()
    root = args.weights_dir
    clips, visuals, syncs = video_frames(args.video, args.duration, modules)

    # Match upstream create_model_from_pretrained without touching the Hub/cache.
    config = json.loads((root / "clip/open_clip_config.json").read_text())
    clip = open_clip.CLIP(**config["model_cfg"])
    open_clip.load_checkpoint(clip, str(root / "clip/open_clip_pytorch_model.bin"))
    clip = clip.eval().to("cuda", dtype)
    clip_features = torch.cat([clip.encode_image(batch.to("cuda", dtype), normalize=True)
                               for batch in clips.split(2)]).unsqueeze(0).cpu()
    tokenizer = open_clip.get_tokenizer("ViT-H-14-378-quickgelu")
    tokens = tokenizer([args.prompt, args.negative_prompt]).cuda()
    # Upstream patch_clip returns normalized last hidden states, without projection.
    text = clip.token_embedding(tokens).to(dtype) + clip.positional_embedding.to(dtype)
    text = clip.transformer(text, attn_mask=clip.attn_mask)
    text = torch.nn.functional.normalize(clip.ln_final(text), dim=-1).cpu()
    del clip, tokens, clips
    release(torch)

    cav = modules["CAVMAEST"](audio_length=208, norm_pix_loss=False, modality_specific_depth=11, tr_pos=False)
    cav.load_state_dict(torch.load(root / "ext_weights/cav_mae_st.pth", map_location="cpu", weights_only=True, mmap=True), strict=False)
    cav = cav.eval().to("cuda", dtype)
    visual_features = torch.stack([cav.forward_feat_v(frame.unsqueeze(0).to("cuda", dtype)).mean(dim=1)
                                   for frame in visuals], dim=1).cpu()
    del cav, visuals
    release(torch)

    sync = modules["Synchformer"]()
    sync.load_state_dict(torch.load(root / "ext_weights/synchformer_state_dict.pth", map_location="cpu", weights_only=True, mmap=True))
    sync = sync.eval().to("cuda", dtype)
    # One 16-frame segment per call, same overlap as upstream, batch size one.
    sync_features = torch.cat([sync(syncs[i:i + 16].unsqueeze(0).unsqueeze(0).to("cuda", dtype)).squeeze(1)
                               for i in range(0, len(syncs) - 15, 8)], dim=1).cpu()
    del sync, syncs
    release(torch)

    original_dtype = torch.get_default_dtype()
    try:
        torch.set_default_dtype(dtype)
        net = modules["create_audio_generation_model"]("large_44k")
    finally:
        torch.set_default_dtype(original_dtype)
    state = torch.load(root / "weights/controlfoley.pth", map_location="cpu", weights_only=True, mmap=True)
    net.load_weights(state)
    del state
    net = net.eval().to("cuda", dtype)
    seq = modules["TemporalConfiguration"](args.duration, SAMPLE_RATE, 512)
    net.update_seq_lengths(seq.latent_sequence_length, seq.clip_sequence_length, seq.visual_sequence_length, seq.sync_sequence_length)
    conditions = net.preprocess_conditions(
        clip_features.to("cuda", dtype), visual_features.to("cuda", dtype),
        sync_features.to("cuda", dtype), text[:1].to("cuda", dtype),
        net.get_empty_audio_sequence(1), net.get_empty_timbre_sequence(1))
    empty = net.get_empty_conditions(1, negative_text_features=text[1:].to("cuda", dtype))
    rng = torch.Generator(device="cuda").manual_seed(args.seed)
    noise = torch.randn(1, net.latent_seq_len, net.latent_dim, device="cuda", dtype=dtype, generator=rng)
    fm = modules["FlowMatching"](num_steps=STEPS)
    latent = net.unnormalize(fm.to_data(lambda t, x: net.ode_wrapper(t, x, conditions, empty, 4.5), noise)).float().cpu()
    del net, conditions, empty, noise, clip_features, visual_features, sync_features, text
    release(torch)

    # VAE/vocoder remain float32 for numeric stability; each gets sole residency.
    vae = modules["get_my_vae"]("44k")
    vae.load_state_dict(torch.load(root / "ext_weights/v1-44.pth", map_location="cpu", weights_only=True, mmap=True))
    vae.remove_weight_norm()
    del vae.encoder
    vae = vae.eval().cuda()
    spec = vae.decode(latent.transpose(1, 2).cuda()).cpu()
    del vae, latent
    release(torch)
    vocoder = modules["BigVGAN"](modules["load_hparams_from_json"](root / "vocoder/config.json"), use_cuda_kernel=False)
    vocoder.load_state_dict(torch.load(root / "vocoder/bigvgan_generator.pt", map_location="cpu", weights_only=True, mmap=True)["generator"])
    vocoder.remove_weight_norm()
    audio = vocoder.eval().cuda()(spec.cuda()).float().cpu()[0].transpose(0, 1).numpy()
    target = round(args.duration * SAMPLE_RATE)
    if len(audio) < target or not modules["np"].isfinite(audio).all():
        raise RuntimeError("Inference produced short or non-finite audio")
    return audio[:target], str(dtype).removeprefix("torch."), torch.cuda.max_memory_allocated()


def main(argv=None):
    args = parse_args(argv)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    modules = imports()
    if args.check_imports:
        print(json.dumps({"imports": "ok", "source_revision": SOURCE_REVISION}))
        return
    # Full hashes are intentionally checked before deserializing any public checkpoint.
    weights_manifest = verify(args.weights_dir)
    started = time.monotonic()
    with modules["torch"].inference_mode():
        audio, precision, peak_bytes = generate(args, modules)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    provenance = args.output.with_suffix(args.output.suffix + ".json")
    # Exclusive output opens also protect against races after argument validation.
    with args.output.open("xb") as handle:
        modules["sf"].write(handle, audio, SAMPLE_RATE, format="WAV", subtype="PCM_24")
    result = {"schema_version": 1, "backend": "controlfoley", "mode": "TV2A",
              "source_revision": SOURCE_REVISION, "weights_manifest": weights_manifest,
              "input_video_sha256": sha256(args.video), "output_sha256": sha256(args.output),
              "prompt": args.prompt, "negative_prompt": args.negative_prompt, "seed": args.seed,
              "duration": len(audio) / SAMPLE_RATE, "sample_rate": SAMPLE_RATE,
              "frames": len(audio), "steps": STEPS, "cfg_strength": 4.5,
              "precision": precision, "staged_offload": True,
              "torch_version": modules["torch"].__version__, "peak_cuda_allocated_bytes": peak_bytes,
              "elapsed_seconds": round(time.monotonic() - started, 3)}
    with provenance.open("x") as handle:
        json.dump(result, handle, indent=2)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
