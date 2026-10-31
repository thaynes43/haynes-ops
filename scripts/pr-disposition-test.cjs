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
  if(baseline && runner===run) {
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
  await check('enabled empty commit strings valid',{pr:{auto_merge:{...enabled,commit_title:'',commit_message:''}}},'disposition:auto-merge');
  await check('qbit patch null',{files:['kubernetes/main/apps/downloads/qbittorrent/app/helmrelease.yaml'],pr:{labels:[{name:'type/patch'}]}},'disposition:stranded');
  await check('qbit patch enabled',{files:['kubernetes/main/apps/downloads/qbittorrent/app/helmrelease.yaml'],pr:{labels:[{name:'type/patch'}],auto_merge:enabled}},'disposition:auto-merge');
  await check('qbit minor manual',{files:['kubernetes/main/apps/downloads/qbittorrent/app/helmrelease.yaml']},'disposition:manual-only');
  await check('plex patch shepherd',{files:['kubernetes/main/apps/media/plex/app/helmrelease.yaml'],pr:{labels:[{name:'type/patch'}]}},'disposition:shepherd');
  await check('missing merge field',{missingMerge:true},'disposition:stranded','merge-state-unavailable');
  for(const value of [false,true,[],{},'enabled',{enabled_by:{login:'renovate[bot]'},merge_method:'bogus'},{enabled_by:{login:'renovate[bot]'},merge_method:'squash'},{...enabled,commit_title:undefined},{...enabled,commit_message:null},{...enabled,commit_title:7}])await check('invalid merge '+JSON.stringify(value),{pr:{auto_merge:value}},'disposition:stranded','merge-state-unavailable');
  await check('current hold',{comments:[vet('HOLD')]},'disposition:stranded','current-head-hold');
  await check('enabled outranks hold',{pr:{auto_merge:enabled},comments:[vet('HOLD')]},'disposition:auto-merge','hold conflicts');
  await check('declined current head',{comments:[vet('OUT-OF-SCOPE')]},'disposition:manual-only');
  await check('trusted auto-merge vet owns followup',{comments:[vet('AUTO-MERGE')]},'disposition:shepherd');
  await check('stale hold ignored',{comments:[vet('HOLD',OTHER)]},'disposition:stranded','eligible-without-merge-request');
  await check('untrusted verdict ignored',{comments:[vet('OUT-OF-SCOPE',SHA,'someone-else')]},'disposition:stranded','eligible-without-merge-request');
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
