// Exercise the actual inline workflow; fixtures make no network calls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const workflow = fs.readFileSync(path.join(root, '.github/workflows/pr-disposition.yml'), 'utf8');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const compile = source => new AsyncFunction('github', 'context', 'core', source.split('          script: |\n')[1].split('\n').map(s => s.replace(/^            /, '')).join('\n'));
const run = compile(workflow);
// Optional pinned main source: compare every fixture's complete label API calls.
const baseline = process.env.PR_DISPOSITION_BASELINE ? compile(fs.readFileSync(process.env.PR_DISPOSITION_BASELINE, 'utf8')) : null;
let parityCases = 0, negativeCases = 0;
const config = p => vm.runInNewContext('(' + fs.readFileSync(path.join(root, p), 'utf8') + ')');
const rules = config('.renovate/autoMerge.json5').packageRules;
const groups = config('.renovate/groups.json5').packageRules;
const SHA = 'a'.repeat(40), OTHER = 'b'.repeat(40);
const enabled = { enabled_by: { login: 'renovate[bot]' }, merge_method: 'squash', commit_title: 'Merge fixture', commit_message: 'Synthetic fixture only' };
let cases = 0;
async function classify(options={}, runner=run) {
  const writes=[], logs=[], labelCalls=[], readCalls=[];
  const pr={number:3707,state:'open',user:{login:'renovate[bot]'},labels:[{name:'type/minor'}],
    head:{ref:'renovate/test-image',sha:SHA},title:'Update image',auto_merge:null,changed_files:1,comments:(options.comments || []).length,...options.pr};
  if(options.missingMerge)delete pr.auto_merge;
  const files=(options.files || ['kubernetes/main/apps/media/tautulli/app/helmrelease.yaml']).map(filename=>({filename}));
  const comments=options.comments || [];
  let reads=0;
  const listedItem=options.listed?{...pr,...options.listed}:pr;
  const rest={pulls:{list(){},listFiles(){},async get(){
    readCalls.push('pr');reads++;if(options.fail==='pr')throw Error('fixture failed PR read');
    return {data: reads>1 && options.latest ? {...pr,...options.latest}:pr};
  }},issues:{listComments(){},async removeLabel(d){labelCalls.push({method:'removeLabel',...d});writes.push({kind:'remove',name:d.name});},
    async addLabels(d){labelCalls.push({method:'addLabels',...d});writes.push({kind:'add',labels:d.labels});}}};
  const github={rest,async paginate(method){
    if(method===rest.pulls.list)return [listedItem];
    if(method===rest.pulls.listFiles){readCalls.push('files');if(options.fail==='files')throw Error('fixture failed files');return options.badFiles ?? files;}
    if(method===rest.issues.listComments){readCalls.push('comments');if(options.fail==='comments')throw Error('fixture failed comments');return options.badComments ?? comments;}
    throw Error('Unexpected GitHub request');
  }};
  await runner(github,{repo:{owner:'test',repo:'test'}},{info(s){logs.push(String(s));},warning(s){logs.push(String(s));},error(s){logs.push(String(s));}});
  if(baseline && runner===run && !options.skipParity) {
    const before=await classify(options,baseline);
    assert.equal(JSON.stringify(labelCalls),JSON.stringify(before.labelCalls),'byte-identical label API calls');
    const listedAsShepherd=listedItem.user?.login==='haynes-ops-bot[bot]' && listedItem.head?.ref?.startsWith('shepherd/');
    if(pr.user.login!=='haynes-ops-bot[bot]' && !listedAsShepherd) {
      const legacyLogs=logs.map(line=>{
        if(!line.startsWith('{'))return line;
        const {repository,attempt,claim,claimBasis,...legacy}=JSON.parse(line);
        return JSON.stringify(legacy);
      });
      assert.deepEqual(legacyLogs,before.logs,'existing log fields and values unchanged');
      assert.deepEqual(readCalls,before.readCalls,'no additional per-PR reads');
    }
    parityCases++;
  }
  return {writes,logs,labelCalls,readCalls,label:writes.find(w=>w.kind==='add')?.labels[0]};
}
async function check(name,options,label,reason) {
  const result=await classify(options);cases++;
  assert.equal(result.label,label,name);
  if(reason)assert(result.logs.some(s=>s.includes(reason)),name+': missing reason '+reason);
  return result;
}
const vet=(verdict,sha=SHA,login='haynes-ops-bot[bot]')=>({id:6540904846,body:`<!-- shepherd-vet sha=${sha} -->\nVERDICT: ${verdict}`,user:{login}});
// OP512 step 2: the night lane's comment = the vet marker, the verdict, the lane marker and a JSON record.
const laneVet=(verdict,sha=SHA,login='haynes-ops-bot[bot]')=>({id:6540904900,user:{login},
  body:`<!-- shepherd-vet sha=${sha} -->\nVERDICT: ${verdict} — fixture reason\n<!-- upgrade-lane v1 -->\n\`\`\`json\n${JSON.stringify({schema:'haynes.upgrade-lane/v1',pr:3707,head:sha,lane:verdict})}\n\`\`\`\n`});
// A lane case either CHANGES the label against pinned main (a new mapping) or must
// leave main's label calls byte-identical (today's behaviour kept).
async function laneCheck(name,options,label,reason,changesOutcome) {
  const result=await classify({...options,skipParity:true});cases++;
  assert.equal(result.label,label,name);
  if(reason)assert(result.logs.some(s=>s.includes(reason)),name+': missing reason '+reason);
  if(baseline) {
    const before=await classify({...options,skipParity:true},baseline);
    if(changesOutcome){assert.notEqual(before.label,label,name+': must differ from pinned main');negativeCases++;}
    else{assert.equal(JSON.stringify(result.labelCalls),JSON.stringify(before.labelCalls),name+': must match pinned main');parityCases++;}
  }
  return result;
}
(async()=>{
  // Keep the existing carve-out policy checks; these tests do not change policy.
  for(const [image,file] of [['kometateam/kometa','media/kometa/app/helmrelease.yaml'],['linuxserver/lazylibrarian','downloads/lazylibrarian/app/helmrelease.yaml'],['linuxserver/calibre','downloads/lazylibrarian/app/epub-convert-cronjob.yaml']]) {
    const matching=rules.filter(r=>(r.matchPackageNames||[]).some(p=>p.startsWith('/')&&new RegExp(p.slice(1,-1)).test('docker.io/'+image)));
    assert(matching.length>0 && matching.at(-1).automerge===false,image+': manual policy changed');
    const leaf=rules.findIndex(r=>(r.matchFileNames||[]).includes(`kubernetes/*/apps/${file.split('/')[0]}/**`)&&r.automerge);
    assert(leaf>=0 && rules.indexOf(matching.at(-1))>leaf,image+': carve-out must follow leaf rule');
    for(const type of ['patch','minor','digest','major'])await check(image+' '+type,{files:['kubernetes/main/apps/'+file],pr:{labels:[{name:'type/'+type}]}},'disposition:manual-only');
  }
  await check('3707 eligible but explicit null',{},'disposition:stranded','eligible-without-merge-request');
  await check('4219 enabled positive control',{pr:{auto_merge:enabled}},'disposition:auto-merge','enabled');
  // OP510: GitHub returns commit_message null for Renovate-enabled auto-merges (read live on 4711/4712, 2026-11-09).
  const renovateNull = { enabled_by: { login: 'renovate[bot]' }, merge_method: 'squash', commit_title: 'fix(container): update image (#4711)', commit_message: null };
  await check('renovate enabled with null commit_message',{pr:{auto_merge:renovateNull}},'disposition:auto-merge','enabled');
  await check('enabled null commit_message outranks hold',{pr:{auto_merge:renovateNull},comments:[vet('HOLD')]},'disposition:auto-merge','hold conflicts');
  await check('null commit_message but missing commit_title',{pr:{auto_merge:{...renovateNull,commit_title:undefined}}},'disposition:stranded','merge-state-unavailable');
  await check('null commit_message but empty login',{pr:{auto_merge:{...renovateNull,enabled_by:{login:''}}}},'disposition:stranded','merge-state-unavailable');
  await check('null commit_message but bad merge_method',{pr:{auto_merge:{...renovateNull,merge_method:'bogus'}}},'disposition:stranded','merge-state-unavailable');
  await check('enabled empty commit strings valid',{pr:{auto_merge:{...enabled,commit_title:'',commit_message:''}}},'disposition:auto-merge');
  await check('qbit patch null',{files:['kubernetes/main/apps/downloads/qbittorrent/app/helmrelease.yaml'],pr:{labels:[{name:'type/patch'}]}},'disposition:stranded');
  await check('qbit patch enabled',{files:['kubernetes/main/apps/downloads/qbittorrent/app/helmrelease.yaml'],pr:{labels:[{name:'type/patch'}],auto_merge:enabled}},'disposition:auto-merge');
  await check('qbit minor manual',{files:['kubernetes/main/apps/downloads/qbittorrent/app/helmrelease.yaml']},'disposition:manual-only');
  await check('plex patch shepherd',{files:['kubernetes/main/apps/media/plex/app/helmrelease.yaml'],pr:{labels:[{name:'type/patch'}]}},'disposition:shepherd');
  await check('missing merge field',{missingMerge:true},'disposition:stranded','merge-state-unavailable');
  for(const value of [false,true,[],{},'enabled',{enabled_by:{login:'renovate[bot]'},merge_method:'bogus'},{enabled_by:{login:'renovate[bot]'},merge_method:'squash'},{...enabled,commit_title:undefined},{...enabled,commit_message:7},{...enabled,commit_message:undefined},{...enabled,commit_title:null},{...enabled,commit_title:7}])await check('invalid merge '+JSON.stringify(value),{pr:{auto_merge:value}},'disposition:stranded','merge-state-unavailable');
  await check('current hold',{comments:[vet('HOLD')]},'disposition:stranded','current-head-hold');
  await check('enabled outranks hold',{pr:{auto_merge:enabled},comments:[vet('HOLD')]},'disposition:auto-merge','hold conflicts');
  await check('declined current head',{comments:[vet('OUT-OF-SCOPE')]},'disposition:manual-only');
  await check('trusted auto-merge vet owns followup',{comments:[vet('AUTO-MERGE')]},'disposition:shepherd');
  await check('stale hold ignored',{comments:[vet('HOLD',OTHER)]},'disposition:stranded','eligible-without-merge-request');
  await check('untrusted verdict ignored',{comments:[vet('OUT-OF-SCOPE',SHA,'someone-else')]},'disposition:stranded','eligible-without-merge-request');
  // OP512 step 2: lane verdict tokens from the bot's lane comment at the current head.
  await laneCheck('lane NIGHTLY -> shepherd',{comments:[laneVet('NIGHTLY')]},'disposition:shepherd','current-head-lane-verdict',true);
  await laneCheck('lane SCHEDULED -> shepherd',{comments:[laneVet('SCHEDULED')]},'disposition:shepherd','current-head-lane-verdict',true);
  await laneCheck('lane OWNER-WINDOW -> stranded',{comments:[laneVet('OWNER-WINDOW')],files:['kubernetes/main/apps/kube-system/cilium/app/helmrelease.yaml']},'disposition:stranded','current-head-lane-verdict',true);
  await laneCheck('lane BLOCKED -> manual-only',{comments:[laneVet('BLOCKED')]},'disposition:manual-only','current-head-lane-verdict',false);
  await laneCheck('lane AGENT-SESSION -> manual-only',{comments:[laneVet('AGENT-SESSION')]},'disposition:manual-only','current-head-lane-verdict',false);
  for(const token of ['MERGED','REVERTED','EXCLUDED'])
    await laneCheck('lane '+token+' keeps today\'s handling',{comments:[laneVet(token)]},'disposition:manual-only','current-head-declined',false);
  await laneCheck('lane HOLD keeps today\'s handling',{comments:[laneVet('HOLD')]},'disposition:stranded','current-head-hold',false);
  for(const token of ['NIGHTLY','SCHEDULED','OWNER-WINDOW','AGENT-SESSION'])
    await laneCheck('plain vet '+token+' without the lane marker keeps today\'s handling',{comments:[vet(token)]},'disposition:manual-only','current-head-declined',false);
  for(const login of ['someone-else','haynes-ops-bot','renovate[bot]'])
    await laneCheck('lane NIGHTLY by '+login+' is ignored',{comments:[laneVet('NIGHTLY',SHA,login)]},'disposition:stranded','eligible-without-merge-request',false);
  await laneCheck('lane NIGHTLY for an older head is ignored',{comments:[laneVet('NIGHTLY',OTHER)]},'disposition:stranded','eligible-without-merge-request',false);
  await laneCheck('lane OWNER-WINDOW for an older head is ignored (manual carve-out stays)',{comments:[laneVet('OWNER-WINDOW',OTHER)],files:['kubernetes/main/apps/media/kometa/app/helmrelease.yaml']},'disposition:manual-only','known-policy-carve-out',false);
  await laneCheck('enabled auto-merge outranks a lane verdict',{pr:{auto_merge:enabled},comments:[laneVet('OWNER-WINDOW')]},'disposition:auto-merge','merge-request-enabled',false);
  await laneCheck('unknown merge state outranks a lane verdict',{missingMerge:true,comments:[laneVet('NIGHTLY')]},'disposition:stranded','merge-state-unavailable',false);
  // Toward automation ranks like AUTO-MERGE (after the carve-outs); toward people ranks like HOLD.
  for(const token of ['NIGHTLY','SCHEDULED'])
    await laneCheck('a manual carve-out outranks lane '+token,{comments:[laneVet(token)],files:['kubernetes/main/apps/media/kometa/app/helmrelease.yaml']},'disposition:manual-only','known-policy-carve-out',false);
  await laneCheck('lane NIGHTLY on a shepherd carve-out stays shepherd',{comments:[laneVet('NIGHTLY')],files:['kubernetes/main/apps/media/plex/app/helmrelease.yaml'],pr:{labels:[{name:'type/patch'}]}},'disposition:shepherd','known-policy-carve-out',true);
  await laneCheck('lane OWNER-WINDOW outranks a manual carve-out',{comments:[laneVet('OWNER-WINDOW')],files:['kubernetes/main/apps/media/kometa/app/helmrelease.yaml']},'disposition:stranded','current-head-lane-verdict',true);
  await laneCheck('lane AGENT-SESSION outranks a shepherd ramp path',{comments:[laneVet('AGENT-SESSION')],files:['kubernetes/main/apps/network/traefik/app/helmrelease.yaml']},'disposition:manual-only','current-head-lane-verdict',false);
  // The lane marker counts only on its own line right after VERDICT (not inside free text).
  const freeText=(verdict)=>({id:6540904902,user:{login:'haynes-ops-bot[bot]'},body:`<!-- shepherd-vet sha=${SHA} -->\nVERDICT: ${verdict}\nnotes quoting <!-- upgrade-lane v1 --> from somewhere\n`});
  const trailing=(verdict)=>({id:6540904903,user:{login:'haynes-ops-bot[bot]'},body:`<!-- shepherd-vet sha=${SHA} -->\nVERDICT: ${verdict}\nfree text\n<!-- upgrade-lane v1 -->\n`});
  for(const [name,c] of [['inside a line',freeText('NIGHTLY')],['on a later line',trailing('NIGHTLY')],['owner token on a later line',trailing('OWNER-WINDOW')]])
    await laneCheck('a lane marker '+name+' is not a lane comment',{comments:[c]},'disposition:manual-only','current-head-declined',false);
  await laneCheck('a later lane verdict replaces an earlier shepherd HOLD',{comments:[vet('HOLD'),laneVet('NIGHTLY')]},'disposition:shepherd','current-head-lane-verdict',true);
  await laneCheck('a later shepherd vet without the marker replaces an earlier lane verdict',{comments:[laneVet('NIGHTLY'),{...vet('HOLD'),id:6540904901}]},'disposition:stranded','current-head-hold',false);
  await laneCheck('a lowercase lane token is read like the shepherd tokens',{comments:[laneVet('owner-window')]},'disposition:stranded','current-head-lane-verdict',true);
  {
    const r=await laneCheck('lane verdict provenance',{comments:[laneVet('AGENT-SESSION')]},'disposition:manual-only','current-head-lane-verdict',false);
    const row=r.logs.filter(s=>s.startsWith('{')).map(s=>JSON.parse(s))[0];
    assert.deepEqual(row.attempt.vet,{commentId:6540904900,sha:SHA,verdict:'AGENT-SESSION',lane:'v1'});
    assert.equal(row.predictedOwner,'AgentSession');
  }
  for(const [group,path] of [['home-assistant','home-assistant'],['home-assistant','ha-mcp'],[null,'zigbee2mqtt']]) {
    const glob=`kubernetes/main/apps/home-automation/${path}/**`;
    assert(groups.some(r=>(r.matchFileNames||[]).includes(glob)&&r.automerge===false&&(!group||r.groupName===group)),path+': group manual policy changed');
    await check(path+' manual parity',{files:[glob.replace('**','app/helmrelease.yaml')]},'disposition:manual-only');
  }
  await check('mixed plex plus manual',{files:['kubernetes/main/apps/media/plex/app/helmrelease.yaml','kubernetes/main/apps/media/kometa/app/helmrelease.yaml'],pr:{changed_files:2}},'disposition:manual-only');
  await check('mixed qbit patch plus manual',{files:['kubernetes/main/apps/downloads/qbittorrent/app/helmrelease.yaml','kubernetes/main/apps/home-automation/zigbee2mqtt/app/helmrelease.yaml'],pr:{changed_files:2,labels:[{name:'type/patch'}]}},'disposition:manual-only');
  for(const options of [{pr:{user:{login:'human'}}},{fail:'pr'},{fail:'files'},{fail:'comments'},{pr:{changed_files:2}},{badFiles:null,pr:{changed_files:0}},{badFiles:[{}]},{badComments:{}},{pr:{comments:1}},{latest:{comments:1}},{latest:{head:{ref:'renovate/test-image',sha:OTHER}}},{latest:{auto_merge:enabled}},{latest:{labels:[{name:'type/major'}]}},{latest:{title:'Update thaynes43 image'}},{latest:{head:{ref:'renovate/other',sha:SHA}}},{latest:{changed_files:2}},{latest:{user:{login:'someone-else'}}},{latest:{number:3708}},{latest:{labels:null}},{pr:{labels:[{}]}}]) {
    const result=await classify(options);cases++;assert.deepEqual(result.writes,[],JSON.stringify(options)+': must not mutate labels');
  }
  await check('label order alone does not change classification',{pr:{labels:[{name:'type/minor'},{name:'renovate'}]},latest:{labels:[{name:'renovate'},{name:'type/minor'}]}},'disposition:stranded');
  const records = result => result.logs.filter(s=>s.startsWith('{')).map(s=>JSON.parse(s));
  const shepherd = {user:{login:'haynes-ops-bot[bot]'},head:{ref:'shepherd/test-image',sha:SHA},labels:[{name:'disposition:stranded'}]};
  async function provenanceCheck(name,options,verify,regression=true) {
    verify(await classify(options));cases++;
    if(baseline && regression) {
      const before=await classify(options,baseline);
      assert.throws(()=>verify(before),assert.AssertionError,name+': must fail on pinned main');negativeCases++;
    }
  }
  const identity = record => {
    assert.equal(record.repository,'test/test');
    assert.equal(record.attempt.headSha,SHA);
    assert.equal(record.claim,'Unknown');
    assert.equal(record.claimBasis,'no-supported-upgrade-claim-actor');
  };
  await provenanceCheck('current-head provenance',{comments:[vet('auto-merge')]},result=>{
    const rows=records(result);assert.equal(rows.length,1);identity(rows[0]);
    assert.deepEqual(rows[0].attempt.vet,{commentId:6540904846,sha:SHA,verdict:'AUTO-MERGE'});
    assert.equal(rows[0].predictedOwner,'Shepherd');
  });
  for(const [name,comment] of [['older head',vet('HOLD',OTHER)],['non-bot',vet('HOLD',SHA,'someone-else')]]) {
    await provenanceCheck(name,{comments:[comment]},result=>{
      const rows=records(result);assert.equal(rows.length,1);identity(rows[0]);assert.equal(rows[0].attempt.vet,null);
    });
  }
  await provenanceCheck('Shepherd log only',{pr:shepherd,comments:[vet('HOLD')]},result=>{
    assert.deepEqual(result.labelCalls,[]);
    assert.deepEqual(records(result),[{pullRequest:3707,repository:'test/test',attempt:{headSha:SHA,vet:{commentId:6540904846,sha:SHA,verdict:'HOLD'}},claim:'Unknown',claimBasis:'no-supported-upgrade-claim-actor',disposition:null,labelPolicy:'not-classified'}]);
    assert.deepEqual(result.readCalls,['pr','files','comments','pr']);
    assert(result.logs.includes('Classified 0 open Renovate PR(s).'));
  });
  await provenanceCheck('Shepherd without vet',{pr:shepherd},result=>{
    const rows=records(result);assert.equal(rows.length,1);identity(rows[0]);assert.equal(rows[0].attempt.vet,null);assert.deepEqual(result.labelCalls,[]);
  });
  for(const failure of [{fail:'pr'},{fail:'files'},{fail:'comments'},{pr:{...shepherd,changed_files:2}},{pr:{...shepherd,comments:1}},{latest:{head:{ref:'shepherd/test-image',sha:OTHER}}},{latest:{head:{ref:'other',sha:SHA}}},{latest:{labels:[]}},{latest:{auto_merge:enabled}},{latest:{user:{login:'human'}}}]) {
    await provenanceCheck('Shepherd failed or changed reads',{...failure,pr:failure.pr || shepherd},result=>{
      assert.deepEqual(result.labelCalls,[]);assert.deepEqual(records(result),[]);
      assert(result.logs.some(s=>s.includes('actual=Unknown reason=read-failure')));
    });
  }
  const readFailure=result=>{
    assert.deepEqual(result.labelCalls,[]);assert.deepEqual(records(result),[]);
    assert(result.logs.some(s=>s.includes('actual=Unknown reason=read-failure')));
  };
  const shepherdListed={user:{login:'haynes-ops-bot[bot]'},head:{ref:'shepherd/test-image',sha:SHA}};
  await provenanceCheck('listed Shepherd fetched as Renovate',{listed:shepherdListed},readFailure);
  await provenanceCheck('listed Shepherd fetched as human',{listed:shepherdListed,pr:{user:{login:'human'}}},readFailure);
  await provenanceCheck('listed Renovate fetched as Shepherd',{listed:{user:{login:'renovate[bot]'},head:{ref:'renovate/test-image',sha:SHA}},pr:{user:{login:'haynes-ops-bot[bot]'},head:{ref:'shepherd/test-image',sha:SHA}}},readFailure,false);
  await provenanceCheck('human on a Shepherd branch',{pr:{...shepherd,user:{login:'human'}}},result=>{
    assert.deepEqual(records(result),[]);assert.deepEqual(result.labelCalls,[]);assert.deepEqual(result.readCalls,[]);
  },false);
  await provenanceCheck('Renovate on a shepherd/ ref is classified',{pr:{head:{ref:'shepherd/x',sha:SHA}}},result=>{
    const rows=records(result);assert.equal(rows.length,1);identity(rows[0]);
    assert.equal(rows[0].disposition,'disposition:stranded');assert.equal(result.label,'disposition:stranded');
  });
  await provenanceCheck('last current-head vet wins',{comments:[vet('HOLD'),{...vet('AUTO-MERGE'),id:6540904847}]},result=>{
    const rows=records(result);assert.equal(rows.length,1);identity(rows[0]);
    assert.deepEqual(rows[0].attempt.vet,{commentId:6540904847,sha:SHA,verdict:'AUTO-MERGE'});
  });
  await provenanceCheck('later older-head vet is ignored',{comments:[vet('HOLD'),{...vet('AUTO-MERGE',OTHER),id:6540904847}]},result=>{
    const rows=records(result);assert.equal(rows.length,1);identity(rows[0]);
    assert.deepEqual(rows[0].attempt.vet,{commentId:6540904846,sha:SHA,verdict:'HOLD'});
  });
  await provenanceCheck('vet without a numeric id keeps the key',{comments:[{...vet('HOLD'),id:'x'}]},result=>{
    const rows=records(result);assert.equal(rows.length,1);identity(rows[0]);
    assert.deepEqual(rows[0].attempt.vet,{commentId:null,sha:SHA,verdict:'HOLD'});
  });
  await provenanceCheck('bot outside Shepherd branch',{pr:{...shepherd,head:{ref:'other',sha:SHA}}},result=>{
    assert.deepEqual(records(result),[]);assert.deepEqual(result.labelCalls,[]);assert.deepEqual(result.readCalls,[]);
  },false);
  console.log(`PR disposition truth table: ${cases} cases passed; ${parityCases} byte-identical label-call comparisons; ${negativeCases} new checks rejected pinned main`);
})().catch(error=>{console.error(error);process.exitCode=1;});
