"""Bounded normal Kokoro synthesis, through its discovered local Wyoming API."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import wave

OUT = Path('/home/dev/artifacts/crunchy-butter')
LINES = [('af_heart', 'Can you make butter crunchy?'),
         ('am_puck', 'Only in the butter laboratory!'),
         ('af_heart', "That's not butter. That's a tiny rave!"),
         ('am_puck', 'Behold! Toastless toast.')]
CODE = '''import asyncio,io,json,sys,wave
from wyoming.client import AsyncTcpClient
from wyoming.tts import Synthesize,SynthesizeVoice
from wyoming.audio import AudioStart,AudioChunk,AudioStop
async def main():
 p=json.load(sys.stdin);chunks=[];fmt=None;total=0
 async with AsyncTcpClient('127.0.0.1',10210) as client:
  await client.write_event(Synthesize(text=p['text'],voice=SynthesizeVoice(name=p['voice'])).event())
  while True:
   event=await asyncio.wait_for(client.read_event(),30)
   if event is None: raise RuntimeError('TTS disconnected')
   if AudioStart.is_type(event.type): fmt=AudioStart.from_event(event)
   elif AudioChunk.is_type(event.type):
    part=AudioChunk.from_event(event);total+=len(part.audio)
    if total>2000000: raise RuntimeError('Unexpectedly long speech')
    chunks.append(part.audio)
   elif AudioStop.is_type(event.type): break
   elif event.type=='error': raise RuntimeError('TTS error')
 if fmt is None or not chunks: raise RuntimeError('No speech returned')
 buf=io.BytesIO()
 with wave.open(buf,'wb') as w:
  w.setnchannels(fmt.channels);w.setsampwidth(fmt.width);w.setframerate(fmt.rate);w.writeframes(b''.join(chunks))
 sys.stdout.buffer.write(buf.getvalue())
asyncio.run(asyncio.wait_for(main(),45))
'''

if __name__ == '__main__':
    targets = [OUT/f'voice-{i+1}.wav' for i in range(len(LINES))]
    if any(p.exists() for p in targets) or (OUT/'speech.json').exists():
        raise RuntimeError('Existing speech: inspect before intentionally regenerating')
    records = []
    for i, (voice,text) in enumerate(LINES):
        result = subprocess.run(['kubectl','exec','-i','-n','ai','deploy/kokoro','-c','app','--','python','-c',CODE],
                                input=json.dumps({'voice':voice,'text':text}).encode(),capture_output=True,check=True,timeout=55)
        data=result.stdout
        with wave.open(io.BytesIO(data)) as w:
            duration=w.getnframes()/w.getframerate()
            assert 0.3<duration<3.65, 'Speech must fit its four-second shot without truncation'
            rate=w.getframerate();channels=w.getnchannels()
        with targets[i].open('xb') as f:f.write(data)
        records.append({'text':text,'voice':voice,'file':targets[i].name,'duration_seconds':duration,
                        'rate':rate,'channels':channels,'sha256':hashlib.sha256(data).hexdigest()})
    (OUT/'speech.json').write_text(json.dumps({'service':'existing Kokoro Wyoming','model':'Kokoro-82M v1.0',
                                             'mode':'separate fixed-voice narration, not lip-synced dialogue','lines':records},indent=2)+'\n')
    print(json.dumps(records,indent=2))
