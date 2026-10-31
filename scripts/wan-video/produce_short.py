"""OP340: finite, serial creative render recipe; no cluster configuration writes."""
import json
from pathlib import Path
import subprocess
import time

OUT = Path('/home/dev/artifacts/dual3090-short')
BASE = 'http://127.0.0.1:8080'
REMOTE = ['kubectl', 'exec', '-i', '-n', 'ai', 'deploy/wan-video', '-c', 'api', '--', 'python3', '-c']
STYLE = (' Original cinematic science-fiction miniature, atmospheric rain, cyan and amber lighting, '
         'subtle film grain, slow physically coherent movement, high contrast, no logos, no writing, no violence. '
         'Keep the single main subject in the narrow central third of the frame for a vertical crop, symmetrical composition.')
SHOTS = [
    ('city', 'The city sold us\nbetter dreams.', 'Slow push forward down a deserted rain-soaked futuristic city alley at midnight. A single amber-lit doorway glows dead center between immense dark buildings. Cyan reflections ripple in the puddles.'),
    ('machine', 'One perfect night.\nOne tiny payment.', 'A beautiful ominous dream vending machine stands alone in the exact center of a rainy dark alley. Its round amber glass window slowly illuminates, revealing swirling golden mist. Straight-on slow dolly in, no readable labels.'),
    ('dream', 'At first,\nit was worth it.', 'An impossibly beautiful tiny golden sun floats above a still black ocean inside a dark circular portal centered in the frame. Luminous particles rise gently from the water, the circular portal rim glows cyan. Slow push toward the golden sun.'),
    ('meter', 'Then the meter\nkept running.', 'Extreme close-up of a mysterious circular analog meter in black polished metal, perfectly centered. One thin amber needle slowly turns clockwise over abstract glowing cyan tick marks. Rain droplets slide down the glass, shallow depth of field. No numbers or letters.'),
    ('bottles', 'We paid\nin tomorrows.', 'A tall narrow glass canister stands in the middle of a dark industrial chamber. A beautiful miniature luminous golden sunrise swirls inside its glass. The gold light slowly drains downward through a thin pipe and the canister turns cold blue. Static camera, one centered vessel.'),
    ('blackout', 'Until the city\nforgot how to wake.', 'A towering futuristic building centered in a rain-soaked midnight city, viewed straight upward between dark symmetrical walls. Its warm golden windows switch off in a slow wave from bottom to top, leaving a single golden window glowing in the center. Slow upward camera movement.'),
    ('defiance', 'So we stopped\nbuying sleep.', 'Macro cinematic close-up of a single dark metal power cable plugged into a round socket in the center of the frame. A tiny golden wildflower grows from a crack beside it, its delicate petals opening as the socket cyan light slowly fades. Rain droplets, gentle breeze, no people.'),
    ('dawn', 'And stole back\nthe dawn.', 'A tiny golden wildflower stands alone dead center on wet dark pavement between futuristic buildings. The first real amber sunrise spills through the narrow alley behind it, replacing cold cyan night with warm light. Gentle breeze moves the flower, slow low-angle push in, hopeful cinematic ending.'),
]


def call(path, body=None, binary=False):
    payload = json.dumps({'url': BASE + path, 'body': body})
    code = ('import json,sys,urllib.request; p=json.load(sys.stdin); '
            'd=None if p["body"] is None else json.dumps(p["body"]).encode(); '
            'r=urllib.request.urlopen(urllib.request.Request(p["url"],data=d,headers={"Content-Type":"application/json"}),timeout=30); '
            'sys.stdout.buffer.write(r.read())')
    data = subprocess.run(REMOTE + [code], input=payload.encode(), capture_output=True, check=True, timeout=50).stdout
    return data if binary else json.loads(data)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = [{'index': i+1, 'name': s[0], 'caption': s[1], 'prompt': s[2]+STYLE,
                 'seed': 34010+i, 'preset': '720p'} for i,s in enumerate(SHOTS)]
    existing = [OUT/'shots.json']
    existing.extend(OUT/(s['name']+suffix) for s in manifest
                    for suffix in ('.mp4', '.json', '-submission.json'))
    if any(path.exists() for path in existing):
        raise RuntimeError('Existing production evidence: inspect and explicitly plan recovery; never auto-resume')
    (OUT/'shots.json').write_text(json.dumps(manifest, indent=2)+'\n')
    started = time.monotonic()
    for shot in manifest:
        status_path = OUT/(shot['name']+'.json')
        clip_path = OUT/(shot['name']+'.mp4')
        if clip_path.exists():
            raise RuntimeError('Existing output: inspect before intentionally resuming')
        if time.monotonic()-started > 3200:
            raise RuntimeError('Story checkpoint reached; do not submit more work')
        # Let the pair cool naturally between useful jobs. Never change fans/clocks/power.
        for attempt in range(30):
            code = 'import sys; sys.path.insert(0,"/config/video"); import api,json; print(json.dumps(api.gpu_sample()))'
            sample = json.loads(subprocess.run(REMOTE+[code], capture_output=True, check=True, timeout=20).stdout)
            if max(x['temperature_c'] for x in sample['gpus']) < 62 and all(x['utilization_percent'] <= 5 for x in sample['gpus']):
                break
            time.sleep(10)
        else:
            raise RuntimeError('Pair did not return to cool idle; stop')
        if time.monotonic()-started > 3200:
            raise RuntimeError('Story checkpoint reached during cooldown; stop before submission')
        reply = call('/videos', {k: shot[k] for k in ('prompt','preset','seed')})
        (OUT/(shot['name']+'-submission.json')).write_text(json.dumps(reply,indent=2)+'\n')
        job_id = reply['id']
        print(json.dumps({'shot':shot['index'],'name':shot['name'],'job_id':job_id,'event':'submitted'}), flush=True)
        while True:
            job = call('/videos/'+job_id)
            status_path.write_text(json.dumps(job,indent=2)+'\n')
            if job['state'] in ('succeeded','failed'):
                break
            time.sleep(15)
        if job['state'] != 'succeeded':
            raise RuntimeError('Owned job failed; inspect before continuing')
        clip_path.write_bytes(call('/videos/'+job_id+'/file', binary=True))
        peaks = {g['uuid']:max(v['temperature_c'] for s in job['telemetry'] for v in s['gpus'] if v['uuid']==g['uuid']) for g in job['telemetry'][0]['gpus']}
        print(json.dumps({'shot':shot['index'],'event':'downloaded','bytes':clip_path.stat().st_size,'peak_temperature_c':peaks}), flush=True)
    print('All eight original shots saved; no further jobs submitted.', flush=True)


if __name__ == '__main__':
    main()
