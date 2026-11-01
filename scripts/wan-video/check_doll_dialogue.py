"""OP345 one intelligibility transcription of our own completed synthetic movie."""
import json,re,subprocess
from pathlib import Path
from check_butter_dialogue import CODE
from edit_short import ffmpeg
from doll_speech import OUT
if __name__=='__main__':
    target=OUT/'dialogue-transcript.json';wav=OUT/'dialogue-check.wav'
    if target.exists() or wav.exists():raise FileExistsError('Transcription already attempted')
    ffmpeg(['-i',str(OUT/'crunchy-butter-dolls.mp4'),'-vn','-ar','16000','-ac','1','-c:a','pcm_s16le',str(wav)])
    with wav.open('rb') as f:
        r=subprocess.run(['kubectl','exec','-i','-n','ai','deploy/whisper','-c','app','--','python','-c',CODE],stdin=f,capture_output=True,check=True,timeout=55)
    report=json.loads(r.stdout);expected=' '.join(x['text'] for x in json.loads((OUT/'speech.json').read_text())['lines'])
    normalize=lambda s:re.sub('[^a-z0-9]','',s.lower())
    report.update(expected=expected,normalized_match=normalize(expected)==normalize(report['transcript']),recognizer='existing local Wyoming ASR, no benchmark',source='crunchy-butter-dolls.mp4')
    with target.open('x') as f:json.dump(report,f,indent=2)
    print(json.dumps(report,indent=2))
    assert report['normalized_match'],'Inspect recognition differences before claiming transcript match'
