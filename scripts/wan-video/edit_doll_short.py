"""OP345 rigid toy puppetry: immutable facial art, expression swaps only on cuts.

One static RGBA doll per shot; only whole-layer translation/rotation is permitted.
No morphing, lip-sync, blinking, facial landmarks, generative video or local warps.
CPU/image encoders bounded to two workers. Outputs are create-only per render name.
"""
import argparse,hashlib,json,math,subprocess
from pathlib import Path
import av
import numpy as np
from PIL import Image,ImageFilter,ImageOps,ImageDraw
from edit_butter import read_audio,write_audio
from edit_short import FFMPEG,ffmpeg
from doll_speech import OUT
W,H,FPS,RATE=1080,1920,24,48000
SHOTS=[('a-skeptical.png','a',0.5),('b-smug.png','b',0.5),('a-shocked.png','a',1.35),('b-delighted.png','b',0.5)]

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def levels(path):
    log=ffmpeg(['-i',str(path),'-vn','-af','loudnorm=I=-20:TP=-3:LRA=11:print_format=json','-f','null','-'])
    return json.JSONDecoder().raw_decode(log[log.rfind('{'):])[0]

def audio():
    records=json.loads((OUT/'speech.json').read_text());mix=np.zeros((20*RATE,2),np.float32);timeline=[]
    for i,record in enumerate(records['lines']):
        assert sha(OUT/record['file'])==record['sha256']
        a=read_audio(OUT/record['file']);start=i*5+SHOTS[i][2]
        assert start+len(a)/RATE<(i+1)*5-.25
        rms=max(float(np.sqrt(np.mean(a*a))),1e-8);a*=10**(-22/20)/rms
        edge=240;a[:edge]*=np.linspace(0,1,edge)[:,None];a[-edge:]*=np.linspace(1,0,edge)[:,None]
        n=round(start*RATE);mix[n:n+len(a)]+=a
        timeline.append({**record,'start_s':start,'end_s':start+len(a)/RATE,'shot':i+1})
    write_audio(OUT/'dialogue-only.wav',mix)
    # Reuse a restrained excerpt of the preceding, preserved video-conditioned cue.
    cue_path=Path('/home/dev/artifacts/controlfoley-asmr/crunchy-butter-controlfoley.wav')
    cue=read_audio(cue_path)[round(1.0*RATE):round(1.85*RATE)].copy()
    cue*=10**(-34/20)/max(float(np.sqrt(np.mean(cue*cue))),1e-8)
    cue[:960]*=np.linspace(0,1,960)[:,None];cue[-2400:]*=np.linspace(1,0,2400)[:,None]
    start=round(10.1*RATE);mix[start:start+len(cue)]+=cue
    assert float(np.max(np.abs(mix)))<.95
    write_audio(OUT/'premaster.wav',mix)
    first=levels(OUT/'premaster.wav')
    gain=min(-20-float(first['input_i']),-3.5-float(first['input_tp']))
    ffmpeg(['-i',str(OUT/'premaster.wav'),'-af',f'volume={gain}dB','-ar','48000','-c:a','pcm_s24le',str(OUT/'doll-soundtrack.wav')])
    with (OUT/'timeline.json').open('x') as f:json.dump({'lines':timeline,'sfx':{'source_sha256':sha(cue_path),'source':'OP344 video-conditioned ControlFoley master','excerpt_s':[1,1.85],'start_s':10.1,'rms_dbfs':-34},'master_gain_db':gain},f,indent=2)

def trajectory(i,t):
    # Whole rigid toy moves in short held steps as an unseen player lifts/rocks it.
    # 12fps puppetry inside a24fps locked camera; speech never drives facial pixels.
    q=math.floor(t*12)/12
    knots=[(0,0,16,0),(.25,-12,-12,-1.6),(.5,2,0,.5),(1.1,8,-6,1.2),
           (1.4,-7,3,-.8),(2.0,4,-8,.8),(2.35,-9,1,-1.2),(2.8,5,-5,.5),
           (3.3,-3,2,-.4),(3.8,0,0,0),(5,0,0,0)]
    for lo,hi in zip(knots,knots[1:]):
        if lo[0]<=q<=hi[0]:
            u=(q-lo[0])/(hi[0]-lo[0]);u=u*u*(3-2*u)
            x=lo[1]+u*(hi[1]-lo[1]);y=lo[2]+u*(hi[2]-lo[2]);angle=lo[3]+u*(hi[3]-lo[3]);break
    sign=1 if i%2==0 else -1
    factor=1.35 if i%2==0 else 1.65
    return sign*x*factor,y*1.45,sign*angle*factor

def render():
    background=Image.open(OUT/'set.png').convert('RGB');background=ImageOps.fit(background,(W,H),method=Image.Resampling.LANCZOS)
    command=[FFMPEG,'-hide_banner','-nostdin','-n','-threads','2','-filter_threads','2','-f','rawvideo','-pix_fmt','rgb24','-s',f'{W}x{H}','-r',str(FPS),'-i','pipe:0','-an','-c:v','libx264','-threads','2','-preset','fast','-crf','18','-pix_fmt','yuv420p',str(OUT/'picture.mp4')]
    records=[];frames=[];contact=[]
    with (OUT/'encode.log').open('xb') as log:
        proc=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=log,stderr=log)
        try:
            for i,(name,character,_) in enumerate(SHOTS):
                sprite=Image.open(OUT/name).convert('RGBA');assert sprite.getextrema()[3][0]==0
                source_hash=hashlib.sha256(sprite.tobytes()).hexdigest()
                # Fit hair silhouette, place the waist behind the stationary counter.
                sprite.thumbnail((1050,1370),Image.Resampling.LANCZOS)
                plate=Image.new('RGBA',(W,H));x=(W-sprite.width)//2+(-16 if character=='a' else 16)
                plate.alpha_composite(sprite,(x,1650-sprite.height))
                shadow=Image.new('RGBA',(W,H),(51,27,43,0));shadow.putalpha(plate.getchannel('A').filter(ImageFilter.GaussianBlur(24)).point(lambda v:round(v*.13)))
                bg=background if character=='a' else ImageOps.mirror(background)
                for n in range(5*FPS):
                    dx,dy,angle=trajectory(i,n/FPS)
                    # A single rigid affine transform acts on the entire doll, including face.
                    layer=plate.rotate(angle,resample=Image.Resampling.BICUBIC,center=(W/2,1550),translate=(dx,dy))
                    sh=shadow.rotate(angle,resample=Image.Resampling.BICUBIC,center=(W/2,1550),translate=(dx+12,dy+6))
                    frame=bg.convert('RGBA');frame.alpha_composite(sh);frame.alpha_composite(layer)
                    # Original set's tabletop is foreground; no independently animated face layer.
                    frame.alpha_composite(bg.crop((0,1488,W,H)).convert('RGBA'),(0,1488))
                    frame=frame.convert('RGB');proc.stdin.write(frame.tobytes())
                    frames.append({'frame':i*120+n,'shot':i+1,'sprite':name,'source_rgba_sha256':source_hash,'rigid_transform':{'dx':dx,'dy':dy,'angle_degrees':angle},'facial_deformation':False})
                    if n in (12,60,108):contact.append(frame.resize((270,480)))
                records.append({'shot':i+1,'start_s':i*5,'end_s':(i+1)*5,'character':character,'asset':name,'file_sha256':sha(OUT/name),'source_rgba_sha256':source_hash,'expression_invariant':True})
            proc.stdin.close();code=proc.wait(timeout=120)
            if code:raise RuntimeError('Video encoder failed; inspect own encode.log')
        finally:
            if proc.poll() is None:proc.terminate();proc.wait(timeout=10)
    sheet=Image.new('RGB',(810,1920))
    for i,im in enumerate(contact):sheet.paste(im,((i%3)*270,(i//3)*480))
    sheet.save(OUT/'contact-sheet.jpg',quality=90)
    with (OUT/'shots.json').open('x') as f:json.dump(records,f,indent=2)
    with (OUT/'frame-transforms.json').open('x') as f:json.dump(frames,f,indent=2)
    ffmpeg(['-i',str(OUT/'picture.mp4'),'-i',str(OUT/'doll-soundtrack.wav'),'-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-b:a','192k','-t','20','-movflags','+faststart',str(OUT/'crunchy-butter-dolls.mp4')])

def validate():
    p=OUT/'crunchy-butter-dolls.mp4';c=av.open(str(p));s=c.streams.video[0];s.thread_count=2
    count=sum(1 for _ in c.decode(s));duration=c.duration/av.time_base;c.close()
    assert count==480 and abs(duration-20)<.05
    frames=json.loads((OUT/'frame-transforms.json').read_text())
    for shot in range(1,5):
        group=[f for f in frames if f['shot']==shot]
        assert len(group)==120 and len({f['source_rgba_sha256'] for f in group})==1
        assert all(f['facial_deformation'] is False for f in group)
        assert len({json.dumps(f['rigid_transform'],sort_keys=True) for f in group})>15
    assert frames[0]['source_rgba_sha256']!=frames[240]['source_rgba_sha256']
    assert frames[120]['source_rgba_sha256']!=frames[360]['source_rgba_sha256']
    a=read_audio(p);l=levels(p);assert len(a)>=20*RATE and np.max(np.abs(a))<1 and float(l['input_tp'])<=-2.5
    report={'duration_seconds':duration,'video':{'width':1080,'height':1920,'fps':24,'decoded_frames':count,'face_source_invariant_per_shot':True,'expression_changed_on_each_return':True,'animation':'one immutable RGBA toy per shot; whole-layer rigid affine transforms only; no generative facial motion'},'audio':{'integrated_lufs':float(l['input_i']),'true_peak_dbfs':float(l['input_tp']),'clipped_samples':int(np.count_nonzero(np.abs(a)>=1))},'sha256':sha(p),'bytes':p.stat().st_size,'human_listening_approval':'unverified','human_visual_review':'contact sheet inspected by driving session; no claim of Tom approval'}
    with (OUT/'validation.json').open('x') as f:json.dump(report,f,indent=2)
    print(json.dumps(report,indent=2))
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['audio','render','validate']);stage=parser.parse_args().stage
    guards={'audio':['dialogue-only.wav','premaster.wav','doll-soundtrack.wav','timeline.json'],'render':['picture.mp4','encode.log','shots.json','frame-transforms.json','crunchy-butter-dolls.mp4'],'validate':['validation.json']}
    if any((OUT/n).exists() for n in guards[stage]):raise FileExistsError('Stage outputs already exist')
    {'audio':audio,'render':render,'validate':validate}[stage]()
