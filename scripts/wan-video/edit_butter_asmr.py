"""OP342: clean 20-second reference-linked ASMR edit; two CPU workers."""
import hashlib
import json
import math
from pathlib import Path

import av
import numpy as np
from PIL import Image
from edit_butter import read_audio, write_audio
from edit_short import ffmpeg

OUT=Path('/home/dev/artifacts/crunchy-butter-asmr')


def main():
    # Broad squeeze envelopes follow the four slow squeeze/hold/release takes;
    # retain the generated crunch's microdynamics, without claiming physical Foley.
    a=read_audio(OUT/'asmr-main.wav')[:960000]
    assert len(a)==960000
    phase=(np.arange(len(a))/48000%5)/5
    a*=(0.35+0.65*np.sin(np.pi*phase)**2)[:,None]
    edge=2400
    a[:edge]*=np.linspace(0,1,edge)[:,None]
    a[-edge:]*=np.linspace(1,0,edge)[:,None]
    peak=float(np.max(np.abs(a)))
    if peak>0.8:a*=0.8/peak
    write_audio(OUT/'premaster.wav',a)
    log=ffmpeg(['-i',str(OUT/'premaster.wav'),'-af',
        'highpass=f=100,lowpass=f=14000,loudnorm=I=-20:TP=-3:LRA=11:print_format=json','-ar','48000',
        '-c:a','pcm_s24le',str(OUT/'crunchy-butter-asmr.wav')])
    (OUT/'audio-loudness.json').write_text(json.dumps(json.JSONDecoder().raw_decode(log[log.rfind('{'):])[0],indent=2)+'\n')
    segments=[]
    for i in range(4):
        p=OUT/f'edit-{i}.mp4'
        ffmpeg(['-i',str(OUT/f'shot-{i}.mp4'),'-vf',
            'scale=1080:1920:flags=lanczos,setsar=1,format=yuv420p',
            '-frames:v','120','-an','-c:v','libx264','-threads','2',
            '-preset','fast','-crf','18',str(p)])
        segments.append(p)
    (OUT/'segments.txt').write_text(''.join("file '"+str(p)+"'\n" for p in segments))
    ffmpeg(['-f','concat','-safe','0','-i',str(OUT/'segments.txt'),
        '-i',str(OUT/'crunchy-butter-asmr.wav'),'-map','0:v:0','-map','1:a:0',
        '-c:v','copy','-c:a','aac','-b:a','192k','-t','20','-movflags','+faststart',
        str(OUT/'crunchy-butter-asmr.mp4')])
    p=OUT/'crunchy-butter-asmr.mp4'
    c=av.open(str(p));s=c.streams.video[0];s.thread_count=2
    video={'codec':s.codec_context.name,'width':s.width,'height':s.height,'fps':str(s.average_rate)}
    audio_context=c.streams.audio[0].codec_context
    encoded_audio={'codec':audio_context.name,'rate':audio_context.sample_rate,'channels':audio_context.channels}
    assert encoded_audio=={'codec':'aac','rate':48000,'channels':2}
    images=[];count=0
    for f in c.decode(s):
        if count%120 in (0,60,119):images.append(f.to_image().resize((180,320)))
        count+=1
    duration=c.duration/av.time_base;c.close();video['decoded_frames']=count
    assert video=={'codec':'h264','width':1080,'height':1920,'fps':'24','decoded_frames':480}
    assert abs(duration-20)<0.05 and len(images)==12
    board=Image.new('RGB',(540,1280))
    for i,im in enumerate(images):board.paste(im,((i%3)*180,(i//3)*320))
    board.save(OUT/'contact-sheet.jpg',quality=92)
    a=read_audio(p);peak=float(np.max(np.abs(a)))
    assert len(a)/48000>=20 and peak<1
    log=ffmpeg(['-i',str(p),'-vn','-af','loudnorm=I=-20:TP=-3:LRA=11:print_format=json','-f','null','-'])
    levels=json.JSONDecoder().raw_decode(log[log.rfind('{'):])[0]
    assert float(levels['input_tp'])<=-1
    report={'duration_seconds':duration,'video':video,
        'audio':{**encoded_audio,'decoded_seconds':len(a)/48000,
        'integrated_lufs':float(levels['input_i']),'true_peak_dbfs':float(levels['input_tp']),
        'sample_peak_dbfs':20*math.log10(peak),'clipped_samples':int(np.count_nonzero(np.abs(a)>=1))},
        'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
        'source':'4x576x1024 reference-conditioned 121-frame clips; first120 each, native24fps; upscale1080x1920. No overlays, speech or music.',
        'human_listening_approval':'unverified'}
    (OUT/'validation.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
