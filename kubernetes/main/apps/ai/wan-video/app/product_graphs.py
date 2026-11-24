"""Server-owned graphs for the two video profiles from Studio E0."""
import json

from product import CHUNK, PROFILES


def image_graph(template, job, prompt, negative):
    graph = json.loads(template.read_text())
    graph["4"]["inputs"]["text"] = prompt
    graph["5"]["inputs"]["text"] = negative
    graph["8"]["inputs"].update(width=job["width"], height=job["height"],
                                length=job["frames"], start_image=["13", 0])
    graph["9"]["inputs"].update(seed=job["seed"], steps=PROFILES["i2v"]["steps"])
    graph["12"]["inputs"]["filename_prefix"] = "video/" + job["id"]
    graph["13"] = {"class_type": "LoadImage", "inputs": {"image": "studio-v1/" + job["image_sha256"] + ".png"}}
    return graph


def node(class_type, **inputs):
    return {"class_type": class_type, "inputs": inputs}


def speech_graph(job, prompt, negative):
    # Studio's E0 graph uses the lightning LoRA and one GPU. Extend chunks
    # consume the previous latents, with a deterministic seed per chunk.
    profile = PROFILES["s2v"]
    frames = job["frames"]
    graph = {
        "1": node("UNETLoader", unet_name="wan2.2_s2v_14B_fp8_scaled.safetensors", weight_dtype="default"),
        "2": node("CLIPLoader", clip_name="umt5_xxl_fp8_e4m3fn_scaled.safetensors", type="wan", device="default"),
        "3": node("VAELoader", vae_name="wan_2.1_vae.safetensors"),
        "4": node("CLIPTextEncode", clip=["2", 0], text=prompt),
        "5": node("CLIPTextEncode", clip=["2", 0], text=negative),
        "6": node("ModelSamplingSD3", model=["17", 0], shift=8.0),
        "7": node("AudioEncoderLoader", audio_encoder_name="wav2vec2_large_english_fp16.safetensors"),
        "8": node("LoadAudio", audio="studio-v1/" + job["audio_sha256"] + ".wav"),
        "9": node("AudioEncoderEncode", audio_encoder=["7", 0], audio=["8", 0]),
        "10": node("LoadImage", image="studio-v1/" + job["image_sha256"] + ".png"),
        "17": node("LoraLoaderModelOnly", model=["1", 0],
                   lora_name="wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors", strength_model=1.0),
    }
    conditioning = {
        "positive": ["4", 0], "negative": ["5", 0], "vae": ["3", 0],
        "audio_encoder_output": ["9", 0], "ref_image": ["10", 0],
    }
    sampler = {
        "model": ["6", 0], "steps": profile["steps"], "cfg": float(profile["cfg"]),
        "sampler_name": "uni_pc", "scheduler": "simple", "denoise": 1.0,
    }
    graph["11"] = node("WanSoundImageToVideo", width=job["width"], height=job["height"],
                       length=min(CHUNK, frames), batch_size=1, **conditioning)
    graph["12"] = node("KSampler", positive=["11", 0], negative=["11", 1], latent_image=["11", 2],
                       seed=job["seed"], **sampler)
    latent = ["12", 0]
    for chunk in range(1, (frames + CHUNK - 1) // CHUNK):
        extend, sample, concat = (str(n) for n in range(100 + 10 * chunk, 103 + 10 * chunk))
        graph[extend] = node("WanSoundImageToVideoExtend", length=CHUNK, video_latent=latent, **conditioning)
        graph[sample] = node("KSampler", positive=[extend, 0], negative=[extend, 1], latent_image=[extend, 2],
                             seed=job["seed"] + chunk, **sampler)
        graph[concat] = node("LatentConcat", samples1=latent, samples2=[sample, 0], dim="t")
        latent = [concat, 0]
    # Duplicate the first latent, then discard the first three decoded frames
    # to avoid E0's overbaked first frame.
    graph.update({
        "18": node("LatentCut", samples=latent, dim="t", index=0, amount=1),
        "19": node("LatentConcat", samples1=["18", 0], samples2=latent, dim="t"),
        "13": node("VAEDecode", samples=["19", 0], vae=["3", 0]),
        "20": node("ImageFromBatch", image=["13", 0], batch_index=3, length=4096),
        "14": node("CreateVideo", images=["20", 0], fps=float(profile["fps"]), audio=["8", 0]),
        "15": node("SaveVideo", video=["14", 0], filename_prefix="video/" + job["id"], format="mp4", codec="h264"),
    })
    return graph
