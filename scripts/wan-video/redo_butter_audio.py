"""OP343: quieter low-frequency Foley spotted to the existing video; no GPU use."""
import hashlib
import json
from pathlib import Path

import av
import numpy as np
from edit_butter import read_audio, write_audio
from edit_short import ffmpeg

OUT=Path('/home/dev/artifacts/butter-audio-redo')
SOURCE=Path('/home/dev/artifacts/crunchy-butter-asmr/crunchy-butter-asmr.mp4')
RATE=48000
# Manual frame review at0.5s intervals; offsets are editorial estimates.
# Starts/ends approximate visible contact motion, not a repeating five-second bed.
EVENTS=[
    ('first deep compression',0.15,1.55,0.85,0.30,1.0),
    ('continued first press',1.85,2.60,2.20,1.65,0.42),
    ('first release friction',3.10,4.30,3.55,0.80,0.14),
    ('small second press',6.20,6.90,6.50,2.00,0.25),
    ('second release friction',8.35,9.25,8.70,1.00,0.13),
    ('side fold compression',10.75,12.65,11.85,0.45,0.85),
    ('side fingertip pinch',13.05,14.35,13.65,1.90,0.55),
    ('fourth thumb press',15.25,16.15,15.65,0.70,0.50),
    ('fourth small compression',17.25,18.35,17.80,1.75,0.40),
    ('final slow press',19.00,19.85,19.40,0.45,0.38),
]


def video_hash(path):
    c=av.open(str(path));s=c.streams.video[0];h=hashlib.sha256()
    for p in c.demux(s):
        if p.size:h.update(bytes(p))
    c.close();return h.hexdigest()


def high_fraction(a):
    n=4096
    blocks=a[:len(a)//n*n].reshape(-1,n,a.shape[1])
    power=np.abs(np.fft.rfft(blocks*np.hanning(n)[None,:,None],axis=1))**2
    f=np.fft.rfftfreq(n,1/RATE)
    return float(power[:,f>3000,:].sum()/max(float(power.sum()),1e-30))


def main():
    metadata=json.loads((OUT/'sand.json').read_text())
    assert hashlib.sha256((OUT/'sand.wav').read_bytes()).hexdigest()==metadata['audio']['sha256']
    # Drop the cue by~5semitones and sharply roll off its brittle high band.
    # New content is granular sand/cork Foley, not the old rubber/cellophane cue.
    ffmpeg(['-i',str(OUT/'sand.wav'),'-af',
        'aresample=48000,asetrate=36000,aresample=48000,highpass=f=100,lowpass=f=1600,lowpass=f=1600',
        '-ar','48000','-c:a','pcm_s24le',str(OUT/'soft-texture.wav')])
    cue=read_audio(OUT/'soft-texture.wav')
    mix=np.zeros((20*RATE,2),dtype=np.float32)
    spots=[]
    for label,start,end,peak,offset,gain in EVENTS:
        n=round((end-start)*RATE);a=cue[round(offset*RATE):round(offset*RATE)+n].copy()
        assert len(a)==n
        # Normalize each selected grain gently; lower release levels are explicit.
        a*=10**(-29/20)/max(float(np.sqrt(np.mean(a*a))),1e-6)
        t=np.arange(n)/RATE+start
        envelope=np.where(t<=peak,np.clip((t-start)/(peak-start),0,1),np.clip((end-t)/(end-peak),0,1))
        envelope=np.sin(envelope*np.pi/2)**1.5
        a*=envelope[:,None]*gain
        index=round(start*RATE);mix[index:index+n]+=a
        spots.append({'action':label,'start_s':start,'end_s':end,'envelope_peak_s':peak,
            'cue_offset_s':offset,'relative_gain':gain,'timing_basis':'visual half-second frame sheet; editorial approximation'})
    peak=float(np.max(np.abs(mix)))
    if peak>0.28:mix*=0.28/peak
    write_audio(OUT/'premaster.wav',mix)
    log=ffmpeg(['-i',str(OUT/'premaster.wav'),'-af',
        'loudnorm=I=-27:TP=-8:LRA=11:print_format=json','-ar','48000','-c:a','pcm_s24le',str(OUT/'soft-crunch.wav')])
    (OUT/'mastering.json').write_text(json.dumps(json.JSONDecoder().raw_decode(log[log.rfind('{'):])[0],indent=2)+'\n')
    (OUT/'spots.json').write_text(json.dumps(spots,indent=2)+'\n')
    final=OUT/'crunchy-butter-soft-audio.mp4'
    ffmpeg(['-i',str(SOURCE),'-i',str(OUT/'soft-crunch.wav'),'-map','0:v:0','-map','1:a:0',
        '-c:v','copy','-c:a','aac','-b:a','192k','-t','20','-movflags','+faststart',str(final)])
    c=av.open(str(final));s=c.streams.video[0];s.thread_count=2
    count=sum(1 for _ in c.decode(s));duration=c.duration/av.time_base;c.close()
    assert count==480 and abs(duration-20)<0.05
    oldhash=video_hash(SOURCE);newhash=video_hash(final);assert oldhash==newhash
    new=read_audio(final);old=read_audio(SOURCE);assert len(new)>=20*RATE and np.max(np.abs(new))<1
    log=ffmpeg(['-i',str(final),'-vn','-af','loudnorm=I=-27:TP=-8:LRA=11:print_format=json','-f','null','-'])
    levels=json.JSONDecoder().raw_decode(log[log.rfind('{'):])[0]
    assert float(levels['input_tp'])<=-6
    high=high_fraction(new);oldhigh=high_fraction(old);assert high<oldhigh*0.1
    # Long holds between the individually spotted events should stay quiet.
    hold_rms=float(np.sqrt(np.mean(new[round(4.5*RATE):round(6.0*RATE)]**2)))
    assert hold_rms<1e-4
    report={'duration_s':duration,'decoded_frames':count,'video_packet_sha256':newhash,'video_unchanged':True,
        'source_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        'sha256':hashlib.sha256(final.read_bytes()).hexdigest(),'bytes':final.stat().st_size,
        'audio':{'integrated_lufs':float(levels['input_i']),'true_peak_dbtp':float(levels['input_tp']),
        'clipped_samples':int(np.count_nonzero(np.abs(new)>=1)),'energy_above_3khz_fraction':high,
        'previous_energy_above_3khz_fraction':oldhigh,'hold_4_5_to_6_s_rms':hold_rms},
        'sync':'Ten individual motion-linked editorial spots, not a periodic bed; approximate visual timing, not physical Foley.',
        'human_listening_approval':'unverified; Tom rejected preceding mix; this is a new candidate'}
    (OUT/'validation.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))


if __name__=='__main__':main()
