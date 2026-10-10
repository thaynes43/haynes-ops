import assert from 'node:assert/strict';
import { readFile, readdir, lstat } from 'node:fs/promises';
import { createInterface } from 'node:readline';
import { createRequire } from 'node:module';
import { canonical, hash, sha, chapterKey, storeHash, validateApproval, deadline,
  assertNoRemoval, assertPreserved, assertRunAdmission, permits, missingCanonicalWorkPlan } from './protocol.mjs';

const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
const lines = input[Symbol.asyncIterator]();
const emit = (value) => process.stdout.write(`${JSON.stringify(value)}\n`);
let approval, until, seq = 0, previous = '0'.repeat(64), completed = false;
const secrets = Object.entries(process.env).filter(([k, v]) => /TOKEN|KEY|PASSWORD|SECRET/i.test(k) && v?.length > 5).map(([, v]) => v);
function safeError(error) {
  let text = error instanceof Error ? error.message : String(error);
  for (const value of secrets) text = text.split(value).join('[redacted]');
  return text.replace(/([?&](?:apiKey|api-key|token)=)[^&\s]*/gi, '$1[redacted]').slice(0, 500);
}
function guard() { assert(Date.now() < until, 'absolute stage deadline expired'); }
async function bounded(promise, ms) {
  let timer;
  try { return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('bounded protocol timeout')), ms); })]); }
  finally { clearTimeout(timer); }
}
async function journal(type, data) {
  guard();
  const event = { phase: approval.phase, seq: ++seq, previous, type, at: new Date().toISOString(), data };
  const digest = hash(event);
  // Hash the exact canonical bytes, not a re-encoded cross-language numeric value.
  emit({ event: canonical(event), sha256: digest, needsAck: true });
  const ack = await bounded(lines.next(), Math.min(5_000, until - Date.now()));
  assert(!ack.done, 'journal receiver disconnected');
  assert.deepEqual(JSON.parse(ack.value), { phase: approval.phase, seq, sha256: digest }, 'journal ACK identity differs');
  guard();
  previous = digest;
}
async function compiledModules(root, prefix = '', out = {}) {
  for (const entry of await readdir(`${root}/${prefix}`, { withFileTypes: true })) {
    const relative = prefix + entry.name;
    assert(!entry.isSymbolicLink(), 'compiled module tree contains a symlink');
    if (entry.isDirectory()) await compiledModules(root, `${relative}/`, out);
    else if (/\.(?:js|json)$/.test(relative)) {
      const info = await lstat(`${root}/${relative}`);
      assert(info.isFile() && info.size <= 4 * 1024 * 1024, 'compiled module is unsafe or oversized');
      out[relative] = sha(await readFile(`${root}/${relative}`));
      assert(Object.keys(out).length <= 2_000, 'compiled module tree exceeded bound');
    }
  }
  return out;
}

try {
  const first = await bounded(lines.next(), 5_000);
  assert(!first.done, 'private approval missing');
  const envelope = JSON.parse(first.value);
  approval = validateApproval(envelope.approval);
  assert.equal(envelope.workerSha256, approval.helpers.bundleSha256, 'reviewed child source identity differs');
  const hostUntil = Date.parse(envelope.until);
  assert(Number.isFinite(hostUntil) && hostUntil > Date.now(), 'original host deadline is missing or expired');
  until = Math.min(deadline(approval), hostUntil);
  process.title = `libretto-list-stage:${approval.phase}`;
  const timer = setTimeout(() => {
    emit({ type: 'fatal', phase: approval.phase, error: 'absolute child deadline expired', outcome: 'unknown' });
    process.exit(2);
  }, until - Date.now());
  timer.unref();
  await main(envelope.workerSha256);
  completed = true;
  clearTimeout(timer);
} catch (error) {
  emit({ type: 'fatal', phase: approval?.phase, error: safeError(error), outcome: 'unknown' });
  process.exitCode = 2;
} finally {
  // Close the iterator's underlying interface even if the host keeps stdin open.
  input.close();
  if (!completed) process.exit(2);
}

async function main(workerSha256) {
  const root = '/app/dist';
  assert.deepEqual(await compiledModules(root), approval.compiledModules, 'deployed compiled modules changed');
  const { loadConfig } = await import(`${root}/config.js`);
  const { createLogger } = await import(`${root}/logger.js`);
  const { recipeSchema } = await import(`${root}/recipes/schema.js`);
  const { matchWorks, recipeMatchOptions } = await import(`${root}/core/match.js`);
  const { reconcileTarget } = await import(`${root}/core/reconciler.js`);
  const { KavitaTarget } = await import(`${root}/target/kavita.js`);
  const { recipeIdFromDescription, buildCollectionDescription, withUpdatedMarker } = await import(`${root}/target/marker.js`);
  const { createAcquireContext } = await import(`${root}/acquire/acquire.js`);
  const { Cron } = createRequire(`${root}/core/scheduler.js`)('croner');
  const serviceConfig = loadConfig();
  const config = loadConfig({ ...process.env, LAZYLIBRARIAN_URL: '', LAZYLIBRARIAN_API_KEY: '' });
  const log = createLogger('silent');
  assert(config.kavita && config.apiKey && !config.fakeTarget, 'real target configuration required');
  assert.equal(config.lazyLibrarian, undefined);
  assert.equal(createAcquireContext(config, log), undefined, 'child acquisition context must be absent');
  assert.equal(Intl.DateTimeFormat().resolvedOptions().timeZone, approval.timezone, 'native scheduler timezone changed');
  let expectedStore = structuredClone(approval.recipeStore);
  let mutations = 0;
  const state = { librettoOrigin: `http://127.0.0.1:${config.port}`, kavitaOrigin: new URL(config.kavita.url).origin,
    active: undefined, saveRecipe: undefined, listId: undefined, listTitle: undefined, description: undefined, itemKeys: new Map(), itemKeysListId: undefined };
  const originalFetch = globalThis.fetch;
  async function runGuard() {
    guard();
    const info = await lstat(config.runsFile);
    assert(info.isFile() && info.size <= 4 * 1024 * 1024, 'native run store missing or unsafe');
    assertRunAdmission(await readFile(config.runsFile), approval.runsSha256, expectedStore, Cron, approval.timezone, until);
  }
  async function api(path) {
    const response = await fetch(`${state.librettoOrigin}${path}`, { headers: { authorization: `Bearer ${config.apiKey}` } });
    assert(response.ok, 'Libretto read refused');
    return response.json();
  }
  async function storeGuard() {
    const current = await api('/api/recipes');
    assert.equal(current.issues?.length, 0, 'current recipe store has invalid files');
    assert.equal(storeHash(current.recipes), storeHash(expectedStore), 'complete recipe store changed');
  }
  globalThis.fetch = async (resource, init = {}) => {
    guard();
    const url = new URL(typeof resource === 'string' || resource instanceof URL ? resource : resource.url);
    const method = (init.method ?? 'GET').toUpperCase();
    const body = init.body === undefined ? undefined : JSON.parse(String(init.body));
    assert(permits(url, method, body, state), `refused request: ${method} ${url.pathname}`);
    const mutable = method === 'PUT' || url.origin === state.kavitaOrigin && method === 'POST' &&
      !['/api/Plugin/authenticate', '/api/Series/all-v2', '/api/ReadingList/lists'].includes(url.pathname);
    if (mutable) {
      await runGuard();
      await storeGuard();
      await journal('intent', { method, path: url.pathname, body, recipeId: state.active?.recipe.id ?? state.saveRecipe?.id });
      // The durable/native ACK can take seconds. Refuse an observable reconcile or
      // recipe change that appeared while the receiver was preserving the intent.
      await runGuard();
      await storeGuard();
    }
    const timeout = AbortSignal.timeout(Math.max(1, Math.min(10_000, until - Date.now())));
    const response = await originalFetch(resource, { ...init, redirect: 'error',
      signal: init.signal ? AbortSignal.any([init.signal, timeout]) : timeout });
    if (mutable) {
      const raw = await response.clone().text();
      assert(Buffer.byteLength(raw) <= 1024 * 1024, 'mutation response exceeded journal bound');
      await journal('response', { status: response.status, method, path: url.pathname, body: raw });
      assert(response.ok, 'mutation rejected; halt without retry');
      mutations += 1;
      if (url.pathname === '/api/ReadingList/create') {
        const created = JSON.parse(raw);
        assert(Number.isSafeInteger(created.id) && created.id > 0, 'created list identity missing');
        state.listId = created.id;
      }
    }
    if (state.active && url.origin === state.kavitaOrigin && url.pathname === '/api/ReadingList/items' && Number(url.searchParams.get('readingListId')) === state.listId && response.ok) {
      const rows = await response.clone().json();
      state.itemKeys = new Map(rows.map((row) => [row.id, chapterKey(row)]));
      state.itemKeysListId = state.listId;
    }
    return response;
  };
  const cache = { get: async () => undefined, set: async () => {}, delete: async () => {}, prune: async () => { throw new Error('no disk cache writer'); } };
  const target = new KavitaTarget(config.kavita, log, cache);
  let readScope;
  const readChapters = target.currentChapters.bind(target);
  target.currentChapters = async (id) => {
    const rows = await readChapters(id);
    assert(readScope?.chapterSnapshots[id], 'current chapter scope is unapproved');
    assert.equal(hash(rows), hash(readScope.chapterSnapshots[id]), 'current physical chapter identity changed');
    return rows;
  };
  const nativePlan = target.readingListPlan.bind(target);
  target.readingListPlan = async (...args) => {
    const plan = await nativePlan(...args);
    if (readScope?.canonicalPolicy === undefined) return plan;
    const { selectBookChapters } = await import(`${root}/target/kavita-chapters.js`);
    const assignments = new Map();
    for (const id of [...new Set(plan.order.map((row) => String(row.seriesId)))]) {
      const matches = args[2].filter((match) => String(match.itemId) === id);
      const selected = selectBookChapters(id, readScope.chapterSnapshots[id], matches);
      for (const [match, chapterIds] of selected) for (const chapterId of chapterIds) {
        const key = chapterKey({ seriesId: id, chapterId });
        if (!assignments.has(key)) assignments.set(key, new Set());
        assignments.get(key).add(hash(match.work));
      }
    }
    return missingCanonicalWorkPlan(plan, readScope.beforeItems, assignments, readScope.physicalChapterProofs);
  };
  await runGuard();
  await storeGuard();
  assert.equal((await api('/health')).status, 'ok');
  const items = await target.listItems(approval.libraryId);
  const collections = await target.listCollections(approval.libraryId);
  const prepared = [];
  for (const scope of approval.scopes) {
    readScope = scope;
    const recipe = recipeSchema.parse(scope.recipe);
    const matches = matchWorks(scope.works, items, recipeMatchOptions(recipe));
    assert(matches.matchedIds.length > 0, 'no verified held member');
    const plan = await target.readingListPlan(matches.matchedIds, approval.libraryId, matches.matchedWorks, 'sync');
    assert.equal(plan.selective, true, 'only Books chapter lists are permitted');
    assert.deepEqual(plan.order.map(chapterKey), scope.desiredChapters.map(chapterKey), 'fresh chapter plan differs from approval');
    const owned = collections.filter((c) => recipeIdFromDescription(c.description) === recipe.id);
    assert.equal(owned.length, scope.ownedListId === null ? 0 : 1, 'owned list missing or ambiguous; no adoption');
    if (scope.ownedListId === null) {
      assert(!collections.some((c) => c.name.normalize('NFKC').toLowerCase() === recipe.name.normalize('NFKC').toLowerCase()), 'new list name conflicts');
    } else {
      assert.equal(owned[0].id, `readinglist:${scope.ownedListId}`);
      assert.equal(hash(owned[0]), hash(scope.beforeCollection), 'full owned list payload changed');
      assert.equal(hash(await target.readingListItems(scope.ownedListId)), hash(scope.beforeItems), 'full owned items changed');
    }
    assertNoRemoval(scope.beforeItems, plan.order);
    prepared.push({ scope, recipe, matches, owned: owned[0] });
  }
  await journal('before', { pid: process.pid, workerSha256, until: new Date(until).toISOString(),
    phase: approval.phase, recipeStore: expectedStore, scopes: approval.scopes,
    normalAcquisitionConfigured: serviceConfig.lazyLibrarian !== undefined, childAcquisitionConfigured: false });
  for (const { scope, recipe, owned } of prepared) {
    guard();
    readScope = scope;
    await runGuard();
    await storeGuard();
    assert.deepEqual(await compiledModules(root), approval.compiledModules, 'compiled modules changed');
    if (scope.save !== false) {
      state.saveRecipe = recipe;
      const response = await fetch(`${state.librettoOrigin}/api/recipes/${recipe.id}`, {
        method: 'PUT', headers: { authorization: `Bearer ${config.apiKey}`, 'content-type': 'application/json' }, body: JSON.stringify(recipe) });
      assert(response.ok);
      state.saveRecipe = undefined;
      expectedStore = expectedStore.filter((r) => r.id !== recipe.id).concat(recipe);
      await storeGuard();
    }
    state.active = scope;
    state.itemKeys = new Map();
    state.itemKeysListId = undefined;
    state.listId = scope.ownedListId === null ? undefined : scope.ownedListId;
    state.listTitle = owned?.name ?? recipe.name;
    state.description = owned ? withUpdatedMarker(owned.description, recipe.id, recipe.category) : buildCollectionDescription(recipe.id, recipe.category);
    const currentOwned = (await target.listCollections(approval.libraryId)).filter((c) => recipeIdFromDescription(c.description) === recipe.id);
    assert.equal(currentOwned.length, owned ? 1 : 0, 'ownership changed during stage');
    if (owned) {
      assert.equal(hash(currentOwned[0]), hash(scope.beforeCollection), 'owned list changed before write');
      assert.equal(hash(await target.readingListItems(state.listId)), hash(scope.beforeItems), 'owned items changed before write');
    }
    const scopedTarget = { server: 'kavita', listItems: target.listItems.bind(target),
      listCollections: async () => currentOwned, updateCollection: target.updateCollection.bind(target),
      createCollection: target.createCollection.bind(target) };
    // No AcquireContext argument. The deployed adapter owns every target mutation.
    const result = await reconcileTarget(recipe, { server: 'kavita', libraryId: approval.libraryId }, scopedTarget, scope.works, log);
    assert.equal(result.acquisition, undefined, 'unexpected acquisition result');
    assert.equal(result.counts.removed, 0, 'native plan reports removals');
    assert(Number.isSafeInteger(state.listId), 'list identity missing after reconcile');
    const after = await target.readingListItems(state.listId);
    assertPreserved(scope.beforeItems, after, scope.desiredChapters);
    await journal('readback', { recipeId: recipe.id, listId: state.listId, result, items: after });
    state.active = undefined;
    state.itemKeys = new Map();
    state.itemKeysListId = undefined;
    await runGuard();
    await storeGuard();
  }
  await journal('complete', { stage: approval.stage, recipeCount: prepared.length, mutationCount: mutations, recipeStoreSha256: storeHash(expectedStore),
    everyExistingItemIdPreserved: true, childAcquisitionConfigured: false, automaticRetry: false });
}
