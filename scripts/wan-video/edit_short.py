"""Rebuild OP340's original 30-second edit from preserved MP4/WAV masters.

Requires av, pillow, numpy and imageio-ffmpeg in a task-local venv.
CPU and codec workers are bounded to two; no network or GPU work.
"""
import hashlib
import json
import math
from pathlib import Path
import subprocess
import wave

import av
import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageFont

OUT = Path('/home/dev/artifacts/dual3090-short')
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
FONT = '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf'
REGULAR = '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf'


def ffmpeg(args):
    result = subprocess.run([FFMPEG, '-hide_banner', '-nostdin', '-y',
                             '-threads', '2', '-filter_threads', '2',
                             '-filter_complex_threads', '2'] + args,
                            capture_output=True, text=True, check=True)
    return result.stderr


def audio(path):
    with wave.open(str(path)) as f:
        assert f.getsampwidth() == 2 and f.getnchannels() == 2
        rate = f.getframerate()
        samples = np.frombuffer(f.readframes(f.getnframes()), dtype='<i2').reshape(-1, 2).astype(np.float32)/32768
    assert rate == 44100
    # The generated mono/stereo relationship is preserved; only global gain changes.
    rms = float(np.sqrt(np.mean(samples**2)))
    return samples / max(rms, 1e-6)


def write_wav(path, samples, rate=44100):
    assert np.isfinite(samples).all() and np.max(np.abs(samples)) < 1
    with wave.open(str(path), 'wb') as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes((samples*32767).astype('<i2').tobytes())


def mix_audio():
    rate = 44100
    t = np.arange(30*rate, dtype=np.float32)/rate
    city = audio(OUT/'audio-city.wav')[:len(t)]
    city_gain = np.minimum(t/0.5, 1) * np.clip((28-t)/6, 0, 1)
    mixed = city * city_gain[:, None] * 10**(-25/20)
    for name, start, length, db in [('machine', 10, 10, -30), ('dawn', 22.5, 7.5, -25)]:
        cue = audio(OUT/('audio-'+name+'.wav'))[:int(length*rate)]
        ct = np.arange(len(cue))/rate
        envelope = np.minimum(ct/0.65, 1) * np.clip((length-ct)/0.6, 0, 1)
        offset = int(start*rate)
        mixed[offset:offset+len(cue)] += cue * envelope[:, None] * 10**(db/20)
    # Original, explicitly synthesized sub-bass punctuates the cuts and accelerates
    # during the debt reveal. This is a composition recipe, not a borrowed sample.
    for start in list(np.arange(0, 15, 1.875)) + list(np.arange(15, 22.5, 0.9375)):
        n = int(0.5*rate)
        u = np.arange(n)/rate
        beat = (np.sin(2*np.pi*48*u) + 0.2*np.sin(2*np.pi*96*u))
        beat *= np.minimum(u/0.015, 1)*np.exp(-u*12)*0.08
        offset = int(start*rate)
        mixed[offset:offset+n] += beat[:, None]
    mixed *= np.minimum(t/0.03, 1)[:, None] * np.clip((30-t)/0.65, 0, 1)[:, None]
    peak = float(np.max(np.abs(mixed)))
    if peak > 0.8:
        mixed *= 0.8/peak
    write_wav(OUT/'soundtrack-premaster.wav', mixed)
    # Two-pass EBU loudness normalization; preserve the first pass measurement.
    first = ffmpeg(['-i', str(OUT/'soundtrack-premaster.wav'), '-af',
                    'loudnorm=I=-18:TP=-2.5:LRA=9:print_format=json', '-f', 'null', '-'])
    measure = json.JSONDecoder().raw_decode(first[first.rfind('{'):])[0]
    normalizer = ('loudnorm=I=-18:TP=-2.5:LRA=9:linear=true:print_format=json:'
                  f'measured_I={measure["input_i"]}:measured_TP={measure["input_tp"]}:'
                  f'measured_LRA={measure["input_lra"]}:measured_thresh={measure["input_thresh"]}:'
                  f'offset={measure["target_offset"]}')
    second = ffmpeg(['-i', str(OUT/'soundtrack-premaster.wav'), '-af', normalizer,
                     '-ar', '48000', '-c:a', 'pcm_s24le', str(OUT/'dream-debt-soundtrack.wav')])
    (OUT/'audio-loudness.json').write_text(json.dumps({
        'first_pass': measure, 'second_pass': json.JSONDecoder().raw_decode(second[second.rfind('{'):])[0],
        'target_lufs': -18, 'target_true_peak_dbfs': -2.5,
        'audition': 'Objective checks only; not independently auditioned by a human.'}, indent=2)+'\n')


def make_overlay(shot):
    image = Image.new('RGBA', (1080, 1920))
    draw = ImageDraw.Draw(image)
    # Subtle readable caption gradient, leaving the bottom UI region empty.
    for y in range(1050, 1650):
        alpha = int(150 * math.sin(math.pi*(y-1050)/600)**0.7)
        draw.line((0, y, 1079, y), fill=(2, 7, 14, alpha))
    label = ImageFont.truetype(FONT, 29)
    small = ImageFont.truetype(REGULAR, 25)
    caption = ImageFont.truetype(FONT, 80)
    draw.text((84, 182), 'D R E A M   D E B T', font=label, fill=(236, 236, 228), stroke_width=1, stroke_fill=(0, 0, 0))
    draw.text((84, 230), 'AN ORIGINAL SCI-FI MICRO-STORY', font=small, fill=(196, 206, 211), stroke_width=1, stroke_fill=(0, 0, 0))
    for i in range(8):
        x = 84+i*100
        draw.rectangle((x, 285, x+78, 290), fill=(111, 233, 227, 255) if i < shot['index'] else (190, 205, 215, 85))
    lines = shot['caption'].split('\n')
    assert all(draw.textbbox((0, 0), line, font=caption)[2] <= 850 for line in lines)
    for i, line in enumerate(lines):
        draw.text((84, 1270+i*96), line, font=caption, fill=(255, 247, 225),
                  stroke_width=3, stroke_fill=(0, 0, 0, 210))
    if shot['index'] == 8:
        draw.text((84, 1530), 'A FICTIONAL WORLD. A FAMILIAR BILL.', font=small,
                  fill=(244, 219, 168), stroke_width=1, stroke_fill=(0, 0, 0))
    path = OUT/(shot['name']+'-overlay.png')
    image.save(path)
    return path


def edit_shot(shot):
    source = OUT/(shot['name']+'.mp4')
    target = OUT/(shot['name']+'-edit.mp4')
    overlay = make_overlay(shot)
    # 1280x704 landscape source => centered 396x704 portrait crop. Delivery is
    # an upscale, not native 1080p generation. 10/9 retime gives 90 frames.
    crop_x = '540' if shot['name'] == 'defiance' else '(iw-396)/2'
    visual = f'[0:v]crop=396:704:{crop_x}:0,scale=1080:1920:flags=lanczos,setsar=1,setpts=10/9*PTS,fps=24'
    if shot['name'] == 'blackout':
        # The take moves upward but does not reliably extinguish its windows.
        # An editorial fade supplies the narrative blackout; captions stay lit.
        visual += ',fade=t=out:st=2.75:d=0.75'
    visual += '[v];[v][1:v]overlay=0:0:shortest=1:format=auto,format=yuv420p[out]'
    ffmpeg(['-i', str(source), '-loop', '1', '-i', str(overlay),
                '-filter_complex', visual,
                '-map', '[out]', '-frames:v', '90', '-an', '-c:v', 'libx264',
                '-threads', '2', '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p',
                '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709', str(target)])
    return target


def assemble_video(shots, segments):
    concat = OUT/'segments.txt'
    concat.write_text(''.join("file '"+str(p)+"'\n" for p in segments))
    ffmpeg(['-f', 'concat', '-safe', '0', '-i', str(concat),
            '-i', str(OUT/'dream-debt-soundtrack.wav'), '-map', '0:v:0', '-map', '1:a:0',
            '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-t', '30',
            '-movflags', '+faststart', '-metadata', 'title=DREAM DEBT',
            '-metadata', 'comment=Original fictional AI-assisted short. Wan2.2 visuals, Stable Audio SFX, original edit and sound design.',
            str(OUT/'dream-debt-short.mp4')])
    return shots


def edit_video():
    shots = json.loads((OUT/'shots.json').read_text())
    return assemble_video(shots, [edit_shot(shot) for shot in shots])


def validate(shots):
    path = OUT/'dream-debt-short.mp4'
    container = av.open(str(path))
    stream = container.streams.video[0]
    stream.thread_count = 2
    video_info = {'codec': stream.codec_context.name, 'width': stream.width,
                  'height': stream.height, 'fps': str(stream.average_rate),
                  'original_shots': len(shots),
                  'source': '1280x704; 396x704 crop centered except shot7 x=540; Lanczos upscale; 10/9 retime'}
    snapshots = []
    count = 0
    # Every video frame is actually decoded, rather than trusting metadata.
    for frame in container.decode(stream):
        if count % 90 == 45:
            snapshots.append(frame.to_image().resize((270, 480)))
        count += 1
    container.close()
    video_info['decoded_frames'] = count
    assert count == 720 and len(snapshots) == 8
    contact = Image.new('RGB', (4*270, 2*480))
    for i, image in enumerate(snapshots):
        contact.paste(image, ((i%4)*270, (i//4)*480))
    contact.save(OUT/'contact-sheet.jpg', quality=92)
    container = av.open(str(path))
    sound = container.streams.audio[0]
    sound.thread_count = 2
    arrays = [frame.to_ndarray() for frame in container.decode(sound)]
    peak = max(float(np.max(np.abs(x))) for x in arrays)
    samples = sum(x.shape[-1] for x in arrays)
    report = {'file': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
              'bytes': path.stat().st_size, 'duration_seconds': container.duration/av.time_base,
              'video': video_info,
              'audio': {'codec': sound.codec_context.name, 'sample_rate': sound.rate,
                        'channels': sound.codec_context.channels, 'decoded_samples_per_channel': samples,
                        'decoded_peak_dbfs': 20*math.log10(max(peak, 1e-9)),
                        'full_scale_samples': sum(int(np.count_nonzero(np.abs(x)>=1)) for x in arrays)}}
    container.close()
    assert report['video']['width'] == 1080 and report['video']['height'] == 1920
    assert report['video']['codec'] == 'h264' and report['video']['fps'] == '24'
    assert abs(report['duration_seconds']-30) < 0.1
    assert report['audio']['codec'] == 'aac' and report['audio']['sample_rate'] == 48000
    assert report['audio']['channels'] == 2 and abs(samples/48000-30) < 0.1
    assert report['audio']['full_scale_samples'] == 0
    measure = ffmpeg(['-i', str(path), '-vn', '-af',
                      'loudnorm=I=-18:TP=-1.5:LRA=9:print_format=json', '-f', 'null', '-'])
    report['audio']['final_aac_loudness'] = json.JSONDecoder().raw_decode(measure[measure.rfind('{'):])[0]
    assert float(report['audio']['final_aac_loudness']['input_tp']) <= -1.0
    (OUT/'validation.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    mix_audio()
    validate(edit_video())
