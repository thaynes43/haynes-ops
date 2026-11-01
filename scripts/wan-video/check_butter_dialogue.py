"""Transcribe only our own synthetic asset once, with the existing local STT.

Use --mixed for the completed movie. This is an intelligibility check, not a
latency benchmark. Existing transcript records are never overwritten.
"""
import argparse
import json
import re
import subprocess
from edit_butter import OUT
from edit_short import ffmpeg

CODE='''import asyncio,io,json,sys,wave
from wyoming.client import AsyncTcpClient
from wyoming.asr import Transcribe,Transcript
from wyoming.audio import AudioStart,AudioChunk,AudioStop
async def run():
 w=wave.open(io.BytesIO(sys.stdin.buffer.read()),'rb');rate=w.getframerate();width=w.getsampwidth();channels=w.getnchannels()
 async with AsyncTcpClient('127.0.0.1',10300) as c:
  await c.write_event(Transcribe(language='en').event())
  await c.write_event(AudioStart(rate=rate,width=width,channels=channels).event())
  while True:
   b=w.readframes(8000)
   if not b:break
   await c.write_event(AudioChunk(rate=rate,width=width,channels=channels,audio=b).event())
  await c.write_event(AudioStop().event())
  while True:
   e=await asyncio.wait_for(c.read_event(),30)
   if e is None:raise RuntimeError('STT disconnected')
   if Transcript.is_type(e.type):print(json.dumps({'transcript':Transcript.from_event(e).text}));break
   if e.type=='error':raise RuntimeError('STT error')
asyncio.run(asyncio.wait_for(run(),45))
'''

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--mixed',action='store_true');args=parser.parse_args()
    stem='dialogue-mixed' if args.mixed else 'dialogue'
    target=OUT/(stem+'-transcript.json')
    if target.exists():raise RuntimeError('Transcript already exists; inspect before intentionally repeating')
    source=OUT/('crunchy-butter-short.mp4' if args.mixed else 'dialogue-only.wav')
    wav=OUT/(stem+'-check.wav')
    ffmpeg(['-i',str(source),'-vn','-ar','16000','-ac','1','-c:a','pcm_s16le',str(wav)])
    with wav.open('rb') as f:
        result=subprocess.run(['kubectl','exec','-i','-n','ai','deploy/whisper','-c','app','--','python','-c',CODE],
                              stdin=f,capture_output=True,check=True,timeout=55)
    report=json.loads(result.stdout);report.update(source=source.name,recognizer='existing whisper service, configured Parakeet TDT')
    expected=' '.join(x['text'] for x in json.loads((OUT/'speech.json').read_text())['lines'])
    normalize=lambda s:re.sub('[^a-z0-9]','',s.lower())
    report['normalized_match']=normalize(expected)==normalize(report['transcript'])
    with target.open('x') as f:f.write(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    assert report['normalized_match'],'Recognition differed: inspect before claiming intelligibility'
