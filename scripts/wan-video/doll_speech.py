"""OP345 four original doll lines on two explicitly verified existing voices."""
import hashlib,io,json,subprocess,wave
from pathlib import Path
from butter_speech import CODE
OUT=Path('/home/dev/artifacts/crunchy-butter-dolls/v2')
LINES=[('a','bf_emma','Can we make this butter squish crunchy?'),
       ('b','af_bella','Obviously. Tiny foam beads. Maximum drama.'),
       ('a','bf_emma','One tiny bead. Why does it sound expensive?'),
       ('b','af_bella',"That's the sound of luxury. Crunch costs extra.")]
def main():
    if any((OUT/f'voice-{i+1}.wav').exists() for i in range(4)) or (OUT/'speech.json').exists():raise FileExistsError('Speech already exists')
    records=[]
    for i,(character,voice,text) in enumerate(LINES):
        r=subprocess.run(['kubectl','exec','-i','-n','ai','deploy/kokoro','-c','app','--','python','-c',CODE],input=json.dumps({'voice':voice,'text':text}).encode(),capture_output=True,check=True,timeout=55)
        with wave.open(io.BytesIO(r.stdout)) as w:
            duration=w.getnframes()/w.getframerate();assert 0.5<duration<5
            record={'character':character,'voice':voice,'text':text,'duration_seconds':duration,'rate':w.getframerate(),'channels':w.getnchannels(),'sha256':hashlib.sha256(r.stdout).hexdigest(),'file':f'voice-{i+1}.wav'}
        with (OUT/record['file']).open('xb') as f:f.write(r.stdout)
        records.append(record)
    with (OUT/'speech.json').open('x') as f:json.dump({'service':'existing Kokoro Wyoming','model':'Kokoro-82M v1.0','voices_verified_by':'live Wyoming Describe response','facial_animation':False,'lines':records},f,indent=2)
    print(json.dumps(records,indent=2))
if __name__=='__main__':main()
