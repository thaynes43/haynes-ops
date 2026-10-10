import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';

export const EVIDENCE_MS = 300_000;
export const CHILD_MS = 180_000;
export const sha = (bytes) => createHash('sha256').update(bytes).digest('hex');
export const canonical = (value) => JSON.stringify(sort(value));
export const hash = (value) => sha(canonical(value));
export const chapterKey = (row) => `${row.seriesId}:${row.chapterId}`;
export const storeHash = (rows) => hash([...rows].sort((a, b) => a.id.localeCompare(b.id)));
function sort(value) {
  if (Array.isArray(value)) return value.map(sort);
  if (value && typeof value === 'object')
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, sort(value[key])]));
  return value;
}
export function deadline(approval, now = Date.now()) {
  const captured = Date.parse(approval.capturedAt);
  assert(Number.isFinite(captured) && captured <= now, 'invalid original capture time');
  const expiry = captured + EVIDENCE_MS;
  assert(now < expiry, 'original evidence expired');
  return Math.min(expiry, now + CHILD_MS);
}
export function validateApproval(a, now = Date.now()) {
  assert.equal(a.schema, 1);
  assert.equal(a.explicitRootApproval, true, 'exact root phase approval required');
  assert(/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(a.phase), 'phase UUID required');
  deadline(a, now);
  assert(['initial', 'batch'].includes(a.stage), 'unknown stage');
  assert(Array.isArray(a.scopes) && a.scopes.length > 0 && a.scopes.length <= 4, 'scope must contain one to four recipes');
  assert.equal(new Set(a.scopes.map((s) => s.recipe.id)).size, a.scopes.length, 'duplicate scope');
  if (a.stage === 'initial') {
    assert.equal(a.scopes.length, 1, 'initial stage must contain one existing recipe');
    assert.equal(a.scopes[0].save, false, 'initial stage cannot save a recipe');
  } else {
    assert.equal(a.initialReceipt?.reviewed, true, 'reviewed completed initial receipt required');
    assert(/^[0-9a-f]{64}$/.test(a.initialReceipt.sha256), 'initial receipt hash required');
  }
  assert.equal(storeHash(a.recipeStore), a.recipeStoreSha256, 'complete recipe payloads changed');
  assert.equal(new Set(a.recipeStore.map((r) => r.id)).size, a.recipeStore.length, 'duplicate store identity');
  assert(a.native && a.compiledModules && /^[0-9a-f]{64}$/.test(a.runsSha256), 'native/module/run-store bindings required');
  assert(Object.keys(a.compiledModules).length > 0, 'complete compiled-module map required');
  assert.deepEqual(Object.keys(a.helpers ?? {}).sort(), ['bundleSha256', 'launcherSha256', 'protocolSha256', 'workerSha256'], 'reviewed stage helper bindings required');
  assert(Object.values(a.helpers).every((value) => /^[0-9a-f]{64}$/.test(value)), 'reviewed helper SHA required');
  assert(Array.isArray(a.artifacts) && a.artifacts.length > 0, 'source/physical/preservation artifacts required');
  for (const artifact of a.artifacts) {
    assert(/^[0-9a-f]{64}$/.test(artifact.sha256) && artifact.path.startsWith('/'), 'private artifact identity required');
    const started = Date.parse(artifact.capturedAt);
    assert(Number.isFinite(started) && started >= Date.parse(a.capturedAt) && started <= now, 'artifact clock is unbound or older than original start');
  }
  for (const scope of a.scopes) {
    assert(['create', 'update', false].includes(scope.save), 'unknown save operation');
    assert.equal(hash(scope.works), scope.worksSha256, 'canonical source works changed');
    assert(Array.isArray(scope.desiredChapters) && scope.desiredChapters.length > 0, 'positive approved chapter plan required');
    assert.equal(new Set(scope.desiredChapters.map(chapterKey)).size, scope.desiredChapters.length, 'duplicate desired chapter');
    assert(scope.chapterSnapshots && Array.isArray(scope.beforeItems), 'complete current chapter/item snapshots required');
    assert.deepEqual(Object.keys(scope.chapterSnapshots).sort(), [...new Set(scope.desiredChapters.map((r) => String(r.seriesId)))].sort(), 'complete selected series scope required');
    for (const desired of scope.desiredChapters)
      assert(scope.chapterSnapshots[desired.seriesId]?.some((r) => r.id === desired.chapterId), 'desired chapter absent from authoritative snapshot');
    if (scope.ownedListId === null) {
      assert(a.stage === 'batch' && scope.save !== false, 'initial/existing repair cannot create or adopt a list');
      assert.equal(scope.ownedListId, null, 'new list must have no adopted owner');
      assert.equal(scope.beforeItems.length, 0, 'new list cannot adopt old items');
    } else {
      assert(Number.isSafeInteger(scope.ownedListId) && scope.ownedListId > 0, 'existing owned list ID required');
      assert.equal(scope.beforeCollection?.id, `readinglist:${scope.ownedListId}`, 'full original owned list payload required');
    }
    assertNoRemoval(scope.beforeItems, scope.desiredChapters);
    if (scope.canonicalPolicy !== undefined) {
      assert.equal(scope.canonicalPolicy, 'missing-works-only', 'unknown canonical policy');
      assert(Array.isArray(scope.physicalChapterProofs), 'complete physical chapter proofs required');
      assert(a.artifacts.some((artifact) => artifact.sha256 === scope.physicalProofArtifactSha256),
        'physical canonical proof artifact is unbound');
    }
    validateRecipeSave(scope, a.recipeStore, a.libraryId);
  }
  return a;
}
export function validateRecipeSave(scope, store, libraryId) {
  const prior = store.find((r) => r.id === scope.recipe.id);
  const recipe = scope.recipe;
  assert.equal(recipe.variables.ordered, true, 'only ordered book recipes');
  assert.equal(recipe.variables.syncMode, 'sync', 'approved ordered sync plan required');
  assert(recipe.targets.some((t) => t.server === 'kavita' && t.libraryId === libraryId), 'approved Books target absent');
  if (scope.save === false) {
    assert(prior && hash(prior) === hash(recipe), 'existing recipe payload changed');
  } else {
    assert.equal(recipe.variables.acquisitionEnabled, false, 'saved recipe acquisition must be false');
    assert.equal(recipe.variables.schedule, 'manual', 'saved recipe must remain manual');
    if (scope.save === 'create') {
      assert.equal(prior, undefined, 'recipe already exists; no automatic adoption');
      assert.deepEqual(recipe.targets, [{ server: 'kavita', libraryId }], 'new recipe target scope changed');
    } else {
      assert(prior && hash(prior) === hash(scope.originalRecipe), 'original update payload changed');
      assert(!prior.targets.some((t) => t.server === 'kavita' && t.libraryId === libraryId), 'Books target already exists; no replay');
      assert.deepEqual(recipe, { ...prior, targets: [...prior.targets, { server: 'kavita', libraryId }] }, 'update may only append the approved Books target');
    }
  }
}
export function assertNoRemoval(before, desired) {
  assert.equal(new Set(before.map((r) => r.id)).size, before.length, 'duplicate existing item ID');
  assert.equal(new Set(before.map(chapterKey)).size, before.length, 'duplicate existing chapter would be deleted by native sync');
  const wanted = new Set(desired.map(chapterKey));
  assert(before.every((r) => wanted.has(chapterKey(r))), 'plan would remove an existing item');
}
// The same filtered plan feeds preview admission and the native adapter's write.
// Owners come from the deployed selector; strengths come from reviewed physical evidence.
export function missingCanonicalWorkPlan(plan, before, assignments, physicalProofs) {
  assert.equal(plan.selective, true, 'canonical policy requires a Books chapter plan');
  assertNoRemoval(before, plan.order);
  const existing = new Set(before.map(chapterKey));
  const owners = new Map();
  for (const row of plan.order) {
    const key = chapterKey(row), candidates = new Set(assignments.get(key) ?? []);
    assert.equal(candidates.size, 1, 'chapter canonical owner is missing or ambiguous');
    owners.set(key, [...candidates][0]);
  }
  const represented = new Set([...existing].map((key) => owners.get(key)));
  const proofs = new Map();
  for (const proof of physicalProofs) {
    const key = chapterKey(proof);
    assert(!proofs.has(key), 'duplicate physical chapter proof');
    proofs.set(key, proof);
  }
  const chosen = new Map();
  for (const row of plan.order) {
    const key = chapterKey(row), owner = owners.get(key);
    if (existing.has(key) || represented.has(owner)) continue;
    const proof = proofs.get(key);
    assert(proof && proof.canonicalWorkSha256 === owner, 'physical proof canonical owner differs');
    assert(['isbn', 'full-title-and-full-author'].includes(proof.proofRoute), 'unknown physical identity strength');
    assert(Number.isSafeInteger(row.chapterId) && row.chapterId > 0, 'unsafe chapter identity');
    const candidate = { row, rank: proof.proofRoute === 'isbn' ? 1 : 0 };
    const prior = chosen.get(owner);
    if (!prior || candidate.rank > prior.rank || candidate.rank === prior.rank && row.chapterId < prior.row.chapterId)
      chosen.set(owner, candidate);
  }
  const additions = new Set([...chosen.values()].map(({ row }) => chapterKey(row)));
  const order = plan.order.filter((row) => existing.has(chapterKey(row)) || additions.has(chapterKey(row)));
  const expected = new Map();
  for (const row of order) {
    const id = String(row.seriesId);
    if (!expected.has(id)) expected.set(id, []);
    expected.get(id).push(row.chapterId);
  }
  assertNoRemoval(before, order);
  return { ...plan, order, expected };
}
export function assertPreserved(before, after, desired) {
  assert.equal(new Set(after.map((r) => r.id)).size, after.length, 'duplicate readback item ID');
  assert.deepEqual(after.map(chapterKey), desired.map(chapterKey), 'readback order/membership differs');
  const withoutOrder = ({ order, ...rest }) => rest;
  for (const old of before) {
    const current = after.find((r) => r.id === old.id);
    assert(current, 'existing item ID disappeared');
    assert.deepEqual(withoutOrder(current), withoutOrder(old), 'existing item payload changed');
  }
}
export function assertRunAdmission(raw, expectedSha, recipes, Cron, timezone, until, now = Date.now()) {
  assert.equal(sha(raw), expectedSha, 'complete raw native run store changed');
  const runs = JSON.parse(raw);
  assert(Array.isArray(runs), 'malformed native run store');
  assert(runs.every((r) => r && typeof r.id === 'string' && ['ok', 'warn', 'error'].includes(r.status) && typeof r.finishedAt === 'string'), 'running or incomplete native run');
  for (const recipe of recipes) {
    if (!recipe.enabled || recipe.variables.schedule === 'manual') continue;
    const timer = new Cron(recipe.variables.schedule, { paused: true, ...(timezone ? { timezone } : {}) });
    try {
      const next = timer.nextRun(new Date(now));
      assert(next instanceof Date && Number.isFinite(next.getTime()) && next.getTime() > until, 'scheduled reconcile intersects bounded stage');
    } finally { timer.stop(); }
  }
}
export function permits(url, method, body, state) {
  if (url.origin === state.librettoOrigin) {
    if (method === 'GET') return ['/health', '/api/recipes'].includes(url.pathname);
    return method === 'PUT' && state.saveRecipe !== undefined &&
      url.pathname === `/api/recipes/${state.saveRecipe.id}` && hash(body) === hash(state.saveRecipe);
  }
  if (url.origin !== state.kavitaOrigin) return false;
  if (method === 'GET') return ['/api/Library/libraries', '/api/Series/volumes', '/api/Collection', '/api/Series/series-by-collection', '/api/ReadingList/items'].includes(url.pathname);
  if (method !== 'POST') return false;
  if (['/api/Plugin/authenticate', '/api/Series/all-v2', '/api/ReadingList/lists'].includes(url.pathname)) return true;
  const scope = state.active;
  if (!scope) return false;
  if (url.pathname === '/api/ReadingList/create')
    return scope.ownedListId === null && state.listId === undefined && Object.keys(body).length === 1 && body.title === scope.recipe.name;
  if (!Number.isSafeInteger(state.listId) || body?.readingListId !== state.listId) return false;
  if (url.pathname === '/api/ReadingList/update-by-chapter')
    return Object.keys(body).sort().join(',') === 'chapterId,readingListId,seriesId' && scope.desiredChapters.some((r) => chapterKey(r) === chapterKey(body));
  if (url.pathname === '/api/ReadingList/update-position') {
    if (state.itemKeysListId !== state.listId) return false;
    if (Object.keys(body).sort().join(',') !== 'fromPosition,readingListId,readingListItemId,toPosition') return false;
    const key = state.itemKeys.get(body.readingListItemId);
    const wantedPosition = scope.desiredChapters.findIndex((r) => chapterKey(r) === key);
    return key !== undefined && wantedPosition >= 0 && wantedPosition === body.toPosition && Number.isSafeInteger(body.fromPosition) && body.fromPosition >= 0;
  }
  if (url.pathname === '/api/ReadingList/update')
    return Object.keys(body).sort().join(',') === 'promoted,readingListId,summary,title' && body.title === state.listTitle && body.summary === state.description && body.promoted === true;
  // No deletion, series-wide addition, normal apply, provider or acquisition request.
  return false;
}
