"""OP344 preserve native video timing while joining video-conditioned audio windows."""
import argparse,hashlib,json,re
from pathlib import Path
import av
import numpy as np
from edit_butter import read_audio,write_audio
from edit_short import ffmpeg
from redo_butter_audio import video_hash
OUT=Path('/home/dev/artifacts/controlfoley-asmr')
SOURCE=Path('/home/dev/artifacts/crunchy-butter-asmr/crunchy-butter-asmr.mp4')
RATE=48000

def measure(path):
    log=ffmpeg(['-i',str(path),'-vn','-af','loudnorm=I=-27:TP=-8:LRA=11:print_format=json','-f','null','-'])
    return json.JSONDecoder().raw_decode(log[log.rfind('{'):])[0]

def main():
    p=argparse.ArgumentParser();p.add_argument('--labels',nargs='+',required=True);p.add_argument('--name',required=True);args=p.parse_args()
    if not re.fullmatch('[a-z0-9-]{1,40}',args.name):raise ValueError('Invalid output name')
    for suffix in ['-joined.wav','-filtered.wav','.wav','.mp4','-validation.json','-production.json']:
        if (OUT/(args.name+suffix)).exists():raise FileExistsError('Output exists: '+args.name+suffix)
    jobs=[json.loads((OUT/(label+'.json')).read_text()) for label in args.labels]
    end=max(j['start_seconds']+j['duration'] for j in jobs)
    mix=np.zeros((round(end*RATE),2),np.float32);weights=np.zeros(len(mix),np.float32)
    for label,j in zip(args.labels,jobs):
        a=read_audio(OUT/(label+'.wav'));n=round(j['duration']*RATE);a=a[:n];assert len(a)==n
        gain=np.ones(n,np.float32);overlap=2*RATE
        if j['start_seconds']>0:gain[:overlap]=np.linspace(0,1,overlap)
        if j['start_seconds']+j['duration']<end:gain[-overlap:]=np.linspace(1,0,overlap)
        start=round(j['start_seconds']*RATE);mix[start:start+n]+=a*gain[:,None];weights[start:start+n]+=gain
    assert np.min(weights)>0.99
    # Keep absolute timestamps and pitch; only overlaps and gentle tonal mastering.
    mix/=weights[:,None]
    mix*=min(1,0.8/max(float(np.max(np.abs(mix))),1e-9))
    raw=OUT/(args.name+'-joined.wav');write_audio(raw,mix)
    filtered=OUT/(args.name+'-filtered.wav')
    ffmpeg(['-i',str(raw),'-af','highpass=f=65,treble=g=-4:f=3000,lowpass=f=6000','-ar','48000','-c:a','pcm_s24le',str(filtered)])
    levels=measure(filtered);gain=min(6,-27-float(levels['input_i']),-8-float(levels['input_tp']))
    wav=OUT/(args.name+'.wav');ffmpeg(['-i',str(filtered),'-af',f'volume={gain}dB,afade=t=in:d=0.02,afade=t=out:st={end-0.04}:d=0.04','-c:a','pcm_s24le',str(wav)])
    final=OUT/(args.name+'.mp4');full=abs(end-20)<0.01
    video_args=['-c:v','copy'] if full else ['-vf','scale=540:960','-c:v','libx264','-threads','2','-preset','fast','-crf','20']
    ffmpeg(['-i',str(SOURCE),'-i',str(wav),'-map','0:v:0','-map','1:a:0']+video_args+['-c:a','aac','-b:a','192k','-t',str(end),'-movflags','+faststart',str(final)])
    c=av.open(str(final));c.streams.video[0].thread_count=2;frames=sum(1 for _ in c.decode(video=0));duration=c.duration/av.time_base;c.close()
    a=read_audio(final);l=measure(final);same=video_hash(SOURCE)==video_hash(final) if full else None
    assert frames==round(end*24) and abs(duration-end)<0.05 and len(a)>=round(end*RATE)
    assert np.max(np.abs(a))<1 and float(l['input_tp'])<=-6
    if full:assert same
    report={'duration_s':duration,'decoded_frames':frames,'video_unchanged':same,'video_packet_sha256':video_hash(final),'sha256':hashlib.sha256(final.read_bytes()).hexdigest(),'bytes':final.stat().st_size,'source_jobs':[j['id'] for j in jobs],'audio':{'integrated_lufs':float(l['input_i']),'true_peak_dbtp':float(l['input_tp']),'clipped_samples':int(np.count_nonzero(np.abs(a)>=1))},'mastering':{'highpass_hz':65,'treble_db':-4,'lowpass_hz':6000,'gain_db':gain,'pitch_or_time_shift':False},'sync':'Video-conditioned windows at original absolute offsets, two-second overlap crossfades. Perceptual sync and comfort require listening.','human_listening_approval':'unverified'}
    (OUT/(args.name+'-validation.json')).write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':main()
