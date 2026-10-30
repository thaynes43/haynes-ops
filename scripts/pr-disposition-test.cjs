// Exercise the actual inline workflow; fixtures make no network calls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const workflow = fs.readFileSync(path.join(root, '.github/workflows/pr-disposition.yml'), 'utf8');
const script = workflow.split('          script: |\n')[1].split('\n').map(s => s.replace(/^            /, '')).join('\n');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const run = new AsyncFunction('github', 'context', 'core', script);
const config = p => vm.runInNewContext('(' + fs.readFileSync(path.join(root, p), 'utf8') + ')');
const rules = config('.renovate/autoMerge.json5').packageRules;
const groups = config('.renovate/groups.json5').packageRules;
const SHA = 'a'.repeat(40), OTHER = 'b'.repeat(40);
const enabled = { enabled_by: { login: 'renovate[bot]' }, merge_method: 'squash', commit_title: 'Merge fixture', commit_message: 'Synthetic fixture only' };
let cases = 0;
async function classify(options={}) {
  const writes=[], logs=[];
  const pr={number:3707,state:'open',user:{login:'renovate[bot]'},labels:[{name:'type/minor'}],
    head:{ref:'renovate/test-image',sha:SHA},title:'Update image',auto_merge:null,changed_files:1,comments:(options.comments || []).length,...options.pr};
  if(options.missingMerge)delete pr.auto_merge;
  const files=(options.files || ['kubernetes/main/apps/media/tautulli/app/helmrelease.yaml']).map(filename=>({filename}));
  const comments=options.comments || [];
  let reads=0;
  const rest={pulls:{list(){},listFiles(){},async get(){
    reads++;if(options.fail==='pr')throw Error('fixture failed PR read');
    return {data: reads>1 && options.latest ? {...pr,...options.latest}:pr};
  }},issues:{listComments(){},async removeLabel(d){writes.push({kind:'remove',name:d.name});},
    async addLabels(d){writes.push({kind:'add',labels:d.labels});}}};
  const github={rest,async paginate(method){
    if(method===rest.pulls.list)return [pr];
    if(method===rest.pulls.listFiles){if(options.fail==='files')throw Error('fixture failed files');return options.badFiles ?? files;}
    if(method===rest.issues.listComments){if(options.fail==='comments')throw Error('fixture failed comments');return options.badComments ?? comments;}
    throw Error('Unexpected GitHub request');
  }};
  await run(github,{repo:{owner:'test',repo:'test'}},{info(s){logs.push(String(s));},warning(s){logs.push(String(s));},error(s){logs.push(String(s));}});
  return {writes,logs,label:writes.find(w=>w.kind==='add')?.labels[0]};
}
async function check(name,options,label,reason) {
  const result=await classify(options);cases++;
  assert.equal(result.label,label,name);
  if(reason)assert(result.logs.some(s=>s.includes(reason)),name+': missing reason '+reason);
  return result;
}
const vet=(verdict,sha=SHA,login='haynes-ops-bot[bot]')=>({body:`<!-- shepherd-vet sha=${sha} -->\nVERDICT: ${verdict}`,user:{login}});
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
  console.log(`PR disposition truth table: ${cases} cases passed`);
})().catch(error=>{console.error(error);process.exitCode=1;});
