"""OP341 sixteen-second toy film; separate speech, SFX and caption layers."""
import hashlib
import json
import math
from pathlib import Path
import wave

import av
import numpy as np
from PIL import Image,ImageDraw,ImageFont
from edit_short import ffmpeg

OUT=Path('/home/dev/artifacts/crunchy-butter')
FONT='/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf'


def read_audio(path):
    c=av.open(str(path));s=c.streams.audio[0];s.thread_count=2
    resampler=av.AudioResampler(format='fltp',layout='stereo',rate=48000)
    chunks=[]
    for frame in c.decode(s):chunks.extend(f.to_ndarray() for f in resampler.resample(frame))
    chunks.extend(f.to_ndarray() for f in resampler.resample(None));c.close()
    return np.concatenate(chunks,axis=1).T


def write_audio(path,a):
    assert np.isfinite(a).all() and np.max(np.abs(a))<1
    with wave.open(str(path),'wb') as w:
        w.setnchannels(2);w.setsampwidth(2);w.setframerate(48000)
        w.writeframes((a*32767).astype('<i2').tobytes())


def mix():
    rate=48000;n=16*rate
    voices=np.zeros((n,2),dtype=np.float32);duck=np.ones(n,dtype=np.float32)
    speech=json.loads((OUT/'speech.json').read_text())
    for i,line in enumerate(speech['lines']):
        a=read_audio(OUT/line['file']);a*=10**(-19/20)/max(float(np.sqrt(np.mean(a*a))),1e-6)
        offset=int((i*4+0.25)*rate);assert offset+len(a)<(i+1)*4*rate
        # Ramp each edge to remove discontinuities without cutting phonemes.
        edge=min(240,len(a)//4);a[:edge]*=np.linspace(0,1,edge)[:,None];a[-edge:]*=np.linspace(1,0,edge)[:,None]
        voices[offset:offset+len(a)]+=a
        lo=max(0,offset-2400);end=offset+len(a);hi=min(n,end+4800)
        duck[lo:offset]=np.linspace(1,0.28,offset-lo)
        duck[offset:end]=0.28
        duck[end:hi]=np.linspace(0.28,1,hi-end)
    assert np.max(np.abs(voices))<1
    write_audio(OUT/'dialogue-only.wav',voices)
    fx=np.zeros_like(voices)
    for name,start,length in [('squish',0,8),('crunch',8,8)]:
        a=read_audio(OUT/('sfx-'+name+'.wav'))[:length*rate]
        a*=10**(-23/20)/max(float(np.sqrt(np.mean(a*a))),1e-6)
        envelope=np.ones(len(a));fade=2400;envelope[:fade]=np.linspace(0,1,fade);envelope[-fade:]=np.linspace(1,0,fade)
        offset=start*rate;fx[offset:offset+len(a)]+=a*envelope[:,None]
    # A small original three-note toy chime punctuates the last reveal.
    for i,hz in enumerate([523.25,659.25,783.99]):
        u=np.arange(24000)/rate;chime=np.sin(2*np.pi*hz*u)*np.exp(-u*10)*np.minimum(u/0.01,1)*0.065
        offset=int((14.2+i*0.16)*rate);fx[offset:offset+len(chime)]+=chime[:,None]
    result=voices+fx*duck[:,None]
    peak=float(np.max(np.abs(result)))
    if peak>0.8:result*=0.8/peak
    write_audio(OUT/'premaster.wav',result)
    log=ffmpeg(['-i',str(OUT/'premaster.wav'),'-af','loudnorm=I=-18:TP=-3:LRA=9:print_format=json',
                '-ar','48000','-c:a','pcm_s24le',str(OUT/'crunchy-butter-soundtrack.wav')])
    (OUT/'audio-loudness.json').write_text(json.dumps(json.JSONDecoder().raw_decode(log[log.rfind('{'):])[0],indent=2)+'\n')


def overlay(shot):
    im=Image.new('RGBA',(1080,1920));d=ImageDraw.Draw(im)
    d.rounded_rectangle((64,150,960,316),radius=35,fill=(255,247,210,245))
    d.text((95,173),'THE BUTTER LAB',font=ImageFont.truetype(FONT,68),fill=(122,28,109))
    d.text((98,257),'SQUISHY TOYS • NOT FOOD',font=ImageFont.truetype(FONT,29),fill=(45,64,78))
    d.rounded_rectangle((64,360,960,620),radius=32,fill=(255,248,220,245))
    captions=['Can you make\nbutter crunchy?','Only in the\nbutter laboratory!',"That’s not butter.\nThat’s a tiny rave!",'BEHOLD!\nTOASTLESS TOAST.']
    font=ImageFont.truetype(FONT,73)
    for i,line in enumerate(captions[shot['index']-1].split('\n')):
        assert d.textbbox((0,0),line,font=font)[2]<850
        d.text((94,394+i*90),line,font=font,fill=(81,27,93))
    p=OUT/(shot['name']+'-overlay.png');im.save(p);return p


def edit():
    shots=json.loads((OUT/'shots.json').read_text());segments=[]
    for shot in shots:
        p=OUT/(shot['name']+'-edit.mp4');image=overlay(shot)
        ffmpeg(['-i',str(OUT/(shot['name']+'.mp4')),'-loop','1','-i',str(image),'-filter_complex',
                '[0:v]crop=396:704:(iw-396)/2:0,scale=1080:1920:flags=lanczos,setsar=1,setpts=32/27*PTS,fps=24[v];[v][1:v]overlay=shortest=1:format=auto,format=yuv420p[out]',
                '-map','[out]','-frames:v','96','-an','-c:v','libx264','-threads','2','-preset','fast','-crf','18',str(p)])
        segments.append(p)
    (OUT/'segments.txt').write_text(''.join("file '"+str(p)+"'\n" for p in segments))
    ffmpeg(['-f','concat','-safe','0','-i',str(OUT/'segments.txt'),'-i',str(OUT/'crunchy-butter-soundtrack.wav'),
            '-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-b:a','192k','-t','16','-movflags','+faststart',
            '-metadata','title=THE BUTTER LAB — Crunchy Butter',str(OUT/'crunchy-butter-short.mp4')])


def validate():
    p=OUT/'crunchy-butter-short.mp4';c=av.open(str(p));s=c.streams.video[0];s.thread_count=2
    v={'codec':s.codec_context.name,'width':s.width,'height':s.height,'fps':str(s.average_rate)}
    imgs=[];count=0
    for f in c.decode(s):
        if count%96==48:imgs.append(f.to_image().resize((270,480)))
        count+=1
    duration=c.duration/av.time_base;c.close();v['decoded_frames']=count
    assert v=={'codec':'h264','width':1080,'height':1920,'fps':'24','decoded_frames':384}
    assert abs(duration-16)<0.05 and len(imgs)==4
    board=Image.new('RGB',(1080,480))
    for i,im in enumerate(imgs):board.paste(im,(i*270,0))
    board.save(OUT/'contact-sheet.jpg',quality=92)
    a=read_audio(p);peak=float(np.max(np.abs(a)));assert len(a)/48000>=16 and peak<1
    log=ffmpeg(['-i',str(p),'-vn','-af','loudnorm=I=-18:TP=-3:LRA=9:print_format=json','-f','null','-'])
    levels=json.JSONDecoder().raw_decode(log[log.rfind('{'):])[0];assert float(levels['input_tp'])<=-1
    report={'duration_seconds':duration,'video':v,'audio':{'rate':48000,'channels':2,'decoded_seconds':len(a)/48000,
             'integrated_lufs':float(levels['input_i']),'true_peak_dbfs':float(levels['input_tp']),
             'sample_peak_dbfs':20*math.log10(peak),'clipped_samples':int(np.count_nonzero(np.abs(a)>=1))},
             'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
             'source':'4x1280x704, centered396x704crop/upscale, retimed81to96frames; separate fixed-voice TTS, not lip sync',
             'human_listening_approval':'unverified'}
    (OUT/'validation.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))


if __name__=='__main__':
    mix();edit();validate()
