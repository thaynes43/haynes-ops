import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, mkdir, writeFile, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { hash, sha, storeHash } from './protocol.mjs';

// A local external-adapter fixture drives the real child protocol. No network/cluster.
async function fixture(drift, batch = false, policy = false, badCall = false) {
  const dir = await mkdtemp(join(tmpdir(), 'libretto-list-stage-fixture-'));
  const root = join(dir, 'app/dist');
  const recipe = { id: 'example', name: 'Example', enabled: true, targets: [{ server: 'kavita', libraryId: '1' }],
    variables: { ordered: true, syncMode: 'sync', schedule: 'manual', acquisitionEnabled: true } };
  const old = { id: 23, seriesId: 7, chapterId: 11, order: 0, progress: 0.5 };
  const works = policy ? [{ title: 'Existing', authors: ['Author'], identifiers: [] },
    { title: 'Missing', authors: ['Author'], identifiers: [] }] :
    [{ title: 'Example', authors: ['Example Author'], identifiers: [] }];
  const matches = works.map((work) => ({ itemId: '7', work }));
  const collection = { id: 'readinglist:9', libraryId: '1', name: 'Example', description: '[libretto:example]', tags: [], itemIds: ['7'], kind: 'kavita_reading_list' };
  const secondRecipe = { ...recipe, id: 'second', name: 'Second' };
  const secondCollection = { ...collection, id: 'readinglist:10', name: 'Second', description: '[libretto:second]' };
  const secondOld = { ...old, id: 25, progress: 0.75 };
  const recipeStore = batch ? [recipe, secondRecipe] : [recipe];
  const collections = batch ? [collection, secondCollection] : [collection];
  const files = {
    'config.js': `export function loadConfig(e=process.env){return {port:9999,kavita:{url:'http://kavita'},apiKey:'fixture',runsFile:${JSON.stringify(join(dir, 'runs.json'))},lazyLibrarian:e.LAZYLIBRARIAN_URL===''?undefined:{url:'http://ll'}}}`,
    'logger.js': `export const createLogger=()=>({});`,
    'recipes/schema.js': `export const recipeSchema={parse:r=>r};`,
    'core/match.js': `export const recipeMatchOptions=()=>({}); export const matchWorks=()=>({matchedIds:['7'],matchedWorks:${policy ? badCall ? 'undefined' : JSON.stringify(matches) : '[]'}});`,
    'core/reconciler.js': `export async function reconcileTarget(r,l,t){await t.listItems(l.libraryId);const c=(await t.listCollections(l.libraryId))[0];await t.updateCollection(c.id,{});return {counts:{removed:0}};}`,
    'core/scheduler.js': `export {};`,
    'target/marker.js': `export const recipeIdFromDescription=s=>s==='[libretto:example]'?'example':s==='[libretto:second]'?'second':undefined;export const buildCollectionDescription=()=> '[libretto:example]';export const withUpdatedMarker=s=>s;`,
    'acquire/acquire.js': `export const createAcquireContext=c=>c.lazyLibrarian?{}:undefined;`,
    'target/kavita.js': `
export class KavitaTarget {
 async listItems(){return [{id:'7'}]}
 async listCollections(){return ${JSON.stringify(collections)}}
 async currentChapters(){return [{id:11},{id:${drift ? 99 : 12}}${policy ? ',{id:13}' : ''}]}
 async readingListPlan(){await this.currentChapters('7');return {selective:true,expected:new Map([['7',[11,12${policy ? ',13' : ''}]]]),order:[{seriesId:'7',chapterId:12},${policy ? "{seriesId:'7',chapterId:13}," : ''}{seriesId:'7',chapterId:11}]}}
 async readingListItems(id){return (await fetch('http://kavita/api/ReadingList/items?readingListId='+id)).json()}
 async updateCollection(containerId){
  const id=Number(containerId.split(':')[1]);const addedItem=id===9?24:26;
  const plan=await this.readingListPlan(${policy ? "['7'],'1'," + JSON.stringify(matches) : ''});
  ${policy ? "if(plan.order.some(row=>row.chapterId===13)||plan.expected.get('7').includes(13))throw new Error('unfiltered native write plan');" : ''}
  await fetch('http://kavita/api/ReadingList/update-by-chapter',{method:'POST',body:JSON.stringify({readingListId:id,seriesId:7,chapterId:12})});
  await this.readingListItems(id);
  await fetch('http://kavita/api/ReadingList/update-position',{method:'POST',body:JSON.stringify({readingListId:id,readingListItemId:addedItem,fromPosition:1,toPosition:0})});
 }
 async createCollection(){throw new Error('not used')}
}`,
  };
  if (policy) files['target/kavita-chapters.js'] = `export const selectBookChapters=(id,chapters,matches)=>new Map(matches.map(match=>[match,match.work.title==='Existing'?[11]:[12,13]]));`;
  for (const [relative, source] of Object.entries(files)) {
    const path = join(root, relative);
    await mkdir(join(path, '..'), { recursive: true });
    await writeFile(path, source);
  }
  await writeFile(join(dir, 'app/package.json'), '{"type":"module"}');
  const cronDir = join(dir, 'app/node_modules/croner');
  await mkdir(cronDir, { recursive: true });
  await writeFile(join(cronDir, 'index.js'), 'exports.Cron=class {nextRun(){return new Date(Date.now()+86400000)} stop(){}}');
  const runs = '[{"id":"done","status":"ok","finishedAt":"2026-10-09T10:00:00Z"}]';
  await writeFile(join(dir, 'runs.json'), runs);
  const preload = join(dir, 'preload.mjs');
  await writeFile(preload, `
import {appendFile,readFile} from 'node:fs/promises';
const lists={9:[${JSON.stringify(old)}],10:[${JSON.stringify(secondOld)}]};
globalThis.fetch=async (resource,init={})=>{
 const u=new URL(resource);const body=init.body?JSON.parse(init.body):undefined;
 if(u.pathname==='/api/recipes'){const changed=await readFile(${JSON.stringify(join(dir, 'store-drift'))}).then(()=>true,()=>false);const recipes=${JSON.stringify(recipeStore)};return Response.json({issues:[],recipes:changed?recipes.map(recipe=>({...recipe,enabled:false})):recipes})}
 if(u.pathname==='/health')return Response.json({status:'ok'});
 if(u.pathname==='/api/ReadingList/items')return Response.json(lists[u.searchParams.get('readingListId')]);
 if(u.pathname==='/api/ReadingList/update-by-chapter'){await appendFile(${JSON.stringify(join(dir, 'writes'))},'add:'+body.readingListId+'\\n');lists[body.readingListId].push({id:body.readingListId===9?24:26,seriesId:7,chapterId:12,order:1});return Response.json(true)}
 if(u.pathname==='/api/ReadingList/update-position'){await appendFile(${JSON.stringify(join(dir, 'writes'))},'order:'+body.readingListId+'\\n');const items=lists[body.readingListId];const i=items.findIndex(r=>r.id===body.readingListItemId);if(i<0)throw new Error('fixture foreign-list item');items.unshift(items.splice(i,1)[0]);lists[body.readingListId]=items.map((r,order)=>({...r,order}));return Response.json(true)}
 throw new Error('unexpected fixture endpoint');
};`);
  const protocol = await readFile(new URL('./protocol.mjs', import.meta.url));
  const worker = (await readFile(new URL('./worker.mjs', import.meta.url), 'utf8'))
    .replace("from './protocol.mjs';", `from 'data:text/javascript;base64,${protocol.toString('base64')}';`)
    .replace("const root = '/app/dist';", `const root = ${JSON.stringify(root)};`);
  const compiledModules = Object.fromEntries(Object.entries(files).map(([name, source]) => [name, sha(source)]));
  const approval = { schema: 1, explicitRootApproval: true, phase: '11111111-2222-3333-4444-555555555555',
    capturedAt: new Date().toISOString(), stage: 'initial', libraryId: '1', native: {}, compiledModules,
    helpers: { bundleSha256: sha(worker), launcherSha256: 'a'.repeat(64), protocolSha256: sha(protocol), workerSha256: 'b'.repeat(64) },
    runsSha256: sha(runs), timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    recipeStore, recipeStoreSha256: storeHash(recipeStore),
    artifacts: [{ path: '/private/source', sha256: 'a'.repeat(64), capturedAt: new Date().toISOString() }],
    scopes: [{ recipe, save: false, ownedListId: 9, beforeCollection: collection, beforeItems: [old], works, worksSha256: hash(works),
      desiredChapters: [{ seriesId: '7', chapterId: 12 }, { seriesId: '7', chapterId: 11 }], chapterSnapshots: { 7: [{ id: 11 }, { id: 12 }] } }] };
  if (batch) {
    approval.stage = 'batch';
    approval.initialReceipt = { reviewed: true, sha256: 'c'.repeat(64) };
    approval.scopes.push({ ...approval.scopes[0], recipe: secondRecipe, ownedListId: 10, beforeCollection: secondCollection, beforeItems: [secondOld] });
  }
  if (policy) Object.assign(approval.scopes[0], { canonicalPolicy: 'missing-works-only',
    physicalProofArtifactSha256: 'a'.repeat(64), chapterSnapshots: { 7: [{ id: 11 }, { id: 12 }, { id: 13 }] },
    physicalChapterProofs: [12, 13].map((chapterId) => ({ seriesId: '7', chapterId,
      canonicalWorkSha256: hash(works[1]), proofRoute: chapterId === 12 ? 'isbn' : 'full-title-and-full-author' })) });
  return { dir, worker, preload, approval, runsFile: join(dir, 'runs.json') };
}

async function run(drift, mode = 'normal') {
  const f = await fixture(drift, mode === 'batch', mode.startsWith('canonical'), mode === 'canonical-bad-call');
  const child = spawn(process.execPath, ['--import', f.preload, '--input-type=module', '-e', f.worker], { stdio: ['pipe', 'pipe', 'pipe'] });
  const events = [];
  const exited = new Promise((resolve, reject) => { child.once('error', reject); child.once('exit', (code) => resolve(code)); });
  const timer = setTimeout(() => child.kill(), 5_000);
  let stderr = '';
  child.stderr.on('data', (bytes) => { stderr += bytes; });
  child.stdin.write(JSON.stringify({ approval: f.approval, workerSha256: sha(f.worker), until: new Date(Date.now() + 180_000).toISOString() }) + '\n');
  try {
    for await (const line of createInterface({ input: child.stdout })) {
      const frame = JSON.parse(line);
      if (frame.type === 'fatal') { events.push(frame); break; }
      assert.equal(sha(frame.event), frame.sha256);
      const event = JSON.parse(frame.event);
      events.push(event);
      if (event.type === 'intent' && mode === 'lose-ack') child.stdin.end();
      else {
        if (event.type === 'intent' && mode === 'run-drift') await writeFile(f.runsFile, '[{"id":"new","status":"running"}]');
        if (event.type === 'intent' && mode === 'store-drift') await writeFile(join(f.dir, 'store-drift'), 'changed');
        child.stdin.write(JSON.stringify({ phase: event.phase, seq: event.seq, sha256: frame.sha256 }) + '\n');
      }
    }
    child.stdin.end();
    const code = await exited;
    assert.equal(stderr, '');
    const writes = await readFile(join(f.dir, 'writes'), 'utf8').catch((error) => {
      if (error.code === 'ENOENT') return '';
      throw error;
    });
    return { code, events, writes };
  } finally { clearTimeout(timer); child.kill(); await rm(f.dir, { recursive: true, force: true }); }
}

test('real child protocol adds/orders through adapter, completes ACK chain and preserves old ID/payload', async () => {
  const { code, events } = await run(false);
  assert.equal(code, 0);
  assert.deepEqual(events.map((e) => e.type), ['before', 'intent', 'response', 'intent', 'response', 'readback', 'complete']);
  const after = events.find((e) => e.type === 'readback').data.items;
  assert.equal(after[1].id, 23);
  assert.equal(after[1].progress, 0.5);
  assert.equal(events.at(-1).data.everyExistingItemIdPreserved, true);
});
test('canonical policy reaches actual child preflight and native writer; incomplete matcher refuses before intent', async () => {
  const good = await run(false, 'canonical');
  assert.equal(good.code, 0);
  assert.equal(good.writes, 'add:9\norder:9\n');
  assert.deepEqual(good.events.find((event) => event.type === 'readback').data.items.map((row) => row.chapterId), [12, 11]);
  const bad = await run(false, 'canonical-bad-call');
  assert.equal(bad.code, 2);
  assert.equal(bad.writes, '');
  assert.equal(bad.events.some((event) => event.type === 'intent'), false);
  assert.match(bad.events.at(-1).error, /matched-work call is incomplete/);
});
test('changed authoritative chapter refuses actual child before any mutation intent', async () => {
  const { code, events } = await run(true);
  assert.equal(code, 2);
  assert.equal(events.some((e) => e.type === 'intent'), false);
  assert.equal(events.at(-1).type, 'fatal');
});
test('missing durable intent ACK prevents the actual outbound mutation and never retries', async () => {
  const { code, events, writes } = await run(false, 'lose-ack');
  assert.equal(code, 2);
  assert.equal(writes, '');
  assert.deepEqual(events.map((event) => event.type), ['before', 'intent', 'fatal']);
});
test('native run or recipe drift during durable intent ACK prevents the first outbound write', async () => {
  for (const mode of ['run-drift', 'store-drift']) {
    const { code, events, writes } = await run(false, mode);
    assert.equal(code, 2, mode);
    assert.equal(writes, '', mode);
    assert.deepEqual(events.map((event) => event.type), ['before', 'intent', 'fatal'], mode);
  }
});
test('two-scope actual child batch uses each owned list item proof and preserves both original IDs', async () => {
  const { code, events, writes } = await run(false, 'batch');
  assert.equal(code, 0);
  assert.equal(writes, 'add:9\norder:9\nadd:10\norder:10\n');
  const readbacks = events.filter((event) => event.type === 'readback');
  assert.deepEqual(readbacks.map((event) => event.data.listId), [9, 10]);
  assert.deepEqual(readbacks.map((event) => [event.data.items[1].id, event.data.items[1].progress]), [[23, 0.5], [25, 0.75]]);
  assert.equal(events.at(-1).data.recipeCount, 2);
});
