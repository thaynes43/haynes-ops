import assert from 'node:assert/strict';
import { test } from 'node:test';
import { deadline, validateApproval, validateRecipeSave, hash, sha, storeHash,
  assertNoRemoval, assertPreserved, permits, assertRunAdmission, missingCanonicalWorkPlan, validatePhysicalProofArtifacts } from './protocol.mjs';

const now = Date.parse('2026-10-09T16:00:00Z');
const recipe = { id: 'example', name: 'Example', enabled: true, targets: [{ server: 'kavita', libraryId: '1' }],
  variables: { ordered: true, syncMode: 'sync', schedule: 'manual', acquisitionEnabled: true } };
const old = { id: 23, seriesId: 7, chapterId: 11, order: 0, progress: 0.5 };
const desired = [{ seriesId: '7', chapterId: 12 }, { seriesId: '7', chapterId: 11 }];
function approval() {
  const works = [{ title: 'Example', authors: ['Example Author'], identifiers: ['isbn:9780000000000'] }];
  return { schema: 1, explicitRootApproval: true, phase: '11111111-2222-3333-4444-555555555555',
    capturedAt: new Date(now - 20_000).toISOString(), stage: 'initial', libraryId: '1',
    native: {}, compiledModules: { 'target/kavita.js': 'a'.repeat(64) }, runsSha256: 'b'.repeat(64),
    helpers: Object.fromEntries(['bundleSha256', 'launcherSha256', 'protocolSha256', 'workerSha256'].map((key) => [key, 'd'.repeat(64)])),
    artifacts: [{ path: '/private/proof', sha256: 'c'.repeat(64), capturedAt: new Date(now - 10_000).toISOString() }],
    recipeStore: [structuredClone(recipe)], recipeStoreSha256: storeHash([recipe]), scopes: [{
      recipe: structuredClone(recipe), save: false, ownedListId: 9, beforeCollection: { id: 'readinglist:9' },
      beforeItems: [structuredClone(old)], desiredChapters: desired, works, worksSha256: hash(works),
      chapterSnapshots: { 7: [{ id: 11 }, { id: 12 }] } }] };
}
test('initial admits one unchanged existing acquisition-enabled recipe while child acquisition is separate', () => {
  assert.equal(validateApproval(approval(), now).scopes.length, 1);
  assert.equal(recipe.variables.acquisitionEnabled, true);
});
test('original evidence clock limits child and cannot be refreshed by later start', () => {
  assert.equal(deadline(approval(), now), now + 180_000);
  const late = approval();
  late.capturedAt = new Date(now - 250_000).toISOString();
  assert.equal(deadline(late, now), now + 50_000);
  assert.throws(() => deadline(late, now + 50_000), /expired/);
});
test('initial scope cannot grow and batch cannot run without reviewed actual first receipt', () => {
  const a = approval();
  a.scopes.push({ ...a.scopes[0], recipe: { ...recipe, id: 'other' } });
  assert.throws(() => validateApproval(a, now), /one existing/);
  a.stage = 'batch';
  assert.throws(() => validateApproval(a, now), /initial receipt/);
  a.initialReceipt = { reviewed: true, sha256: 'd'.repeat(64) };
  a.scopes = Array.from({ length: 5 }, (_, i) => ({ ...a.scopes[0], recipe: { ...recipe, id: `r${i}` } }));
  assert.throws(() => validateApproval(a, now), /one to four/);
});
test('missing chapter scope and changed source roster refuse before writes', () => {
  const a = approval();
  a.scopes[0].chapterSnapshots = {};
  assert.throws(() => validateApproval(a, now), /selected series/);
  const b = approval();
  b.scopes[0].works[0].authors.push('Other Contributor');
  assert.throws(() => validateApproval(b, now), /source works changed/);
});
test('foreign and duplicate old items refuse native sync plan', () => {
  assertNoRemoval([old], desired);
  assert.throws(() => assertNoRemoval([{ ...old, chapterId: 99 }], desired), /remove/);
  assert.throws(() => assertNoRemoval([old, { ...old, id: 24 }], desired), /duplicate existing chapter/);
});
test('missing-work plan retains existing copies, chooses strongest then lowest chapter, and refuses ambiguous owners', () => {
  const order = [11, 12, 13, 20, 21, 30, 31].map((chapterId) => ({ seriesId: '7', chapterId }));
  const before = [{ ...old }, { ...old, id: 24, chapterId: 12 }];
  const assignments = new Map(order.map((row) => [`7:${row.chapterId}`, [row.chapterId < 20 ? 'old-work' : row.chapterId < 30 ? 'isbn-work' : 'title-work']]));
  const proofs = order.filter((row) => row.chapterId >= 20).map((row) => ({ ...row,
    canonicalWorkSha256: assignments.get(`7:${row.chapterId}`)[0],
    proofRoute: row.chapterId === 21 ? 'isbn' : 'full-title-and-full-author' }));
  const plan = { selective: true, order, expected: new Map([['7', order.map((row) => row.chapterId)]]), fingerprints: new Map([['7', 'unchanged-full-series']]) };
  const preview = missingCanonicalWorkPlan(plan, before, assignments, proofs);
  assert.deepEqual(preview.order.map((row) => row.chapterId), [11, 12, 21, 30]);
  assert.deepEqual(preview.expected.get('7'), [11, 12, 21, 30]);
  assert.equal(preview.fingerprints, plan.fingerprints);
  assert.deepEqual(missingCanonicalWorkPlan(plan, before, assignments, proofs), preview);
  assert.deepEqual(plan.order, order);
  const ambiguous = new Map(assignments); ambiguous.set('7:20', ['isbn-work', 'another-work']);
  assert.throws(() => missingCanonicalWorkPlan(plan, before, ambiguous, proofs), /ambiguous/);
  assert.throws(() => missingCanonicalWorkPlan(plan, before, assignments, []), /physical proof/);
  const foreign = proofs.map((proof) => ({ ...proof, canonicalWorkSha256: 'foreign-work' }));
  assert.throws(() => missingCanonicalWorkPlan(plan, before, assignments, foreign), /canonical owner/);
});
test('inline candidate strengths and owners must equal actual pinned physical proof contents', () => {
  const work = { title: 'Example', authors: ['Example Author'] };
  const physical = { recipes: { example: { verified: true, chapters: [{ seriesId: '7', chapterId: 12,
    canonicalWorkIdentityVerified: true, files: [{ canonicalWork: work, rawIdentityVerified: true,
      fingerprintVerified: true, proofRoute: 'full-title-and-full-author' }] }] } } };
  const a = approval(), scope = a.scopes[0];
  Object.assign(scope, { canonicalPolicy: 'missing-works-only', physicalProofArtifactSha256: 'c'.repeat(64),
    physicalChapterProofs: [{ seriesId: '7', chapterId: 12, canonicalWorkSha256: hash(work), proofRoute: 'full-title-and-full-author' }] });
  const artifacts = { ['c'.repeat(64)]: physical };
  validatePhysicalProofArtifacts(a, artifacts);
  scope.physicalChapterProofs[0].proofRoute = 'isbn';
  assert.throws(() => validatePhysicalProofArtifacts(a, artifacts), /artifact contents/);
  scope.physicalChapterProofs[0].proofRoute = 'full-title-and-full-author';
  scope.physicalChapterProofs[0].canonicalWorkSha256 = hash({ ...work, authors: ['Foreign Author'] });
  assert.throws(() => validatePhysicalProofArtifacts(a, artifacts), /artifact contents/);
});
test('readback preserves each old item ID and every non-order field', () => {
  const after = [{ id: 24, seriesId: 7, chapterId: 12, order: 0 }, { ...old, order: 1 }];
  assertPreserved([old], after, desired);
  assert.throws(() => assertPreserved([old], [after[0], { ...after[1], id: 25 }], desired), /disappeared/);
  assert.throws(() => assertPreserved([old], [after[0], { ...after[1], progress: 0 }], desired), /payload changed/);
});
test('recipe update may append only one Books target preserving ABS and complete original payload', () => {
  const original = { ...recipe, targets: [{ server: 'abs', libraryId: 'audio' }], variables: { ...recipe.variables, acquisitionEnabled: false } };
  const scope = { save: 'update', originalRecipe: original, recipe: { ...original, targets: [...original.targets, { server: 'kavita', libraryId: '1' }] } };
  validateRecipeSave(scope, [original], '1');
  assert.throws(() => validateRecipeSave({ ...scope, recipe: { ...scope.recipe, name: 'Renamed' } }, [original], '1'), /only append/);
});
test('new recipes must stay manual acquisitionfalse and cannot adopt an existing recipe', () => {
  const scope = { save: 'create', recipe: { ...recipe, variables: { ...recipe.variables, acquisitionEnabled: false } } };
  validateRecipeSave(scope, [], '1');
  assert.throws(() => validateRecipeSave(scope, [scope.recipe], '1'), /already exists/);
  assert.throws(() => validateRecipeSave({ ...scope, recipe }, [], '1'), /acquisition must be false/);
});
test('mutation boundary allows exact chapter/order/metadata and rejects every deletion and normal apply', () => {
  const scope = approval().scopes[0];
  const state = { librettoOrigin: 'http://libretto', kavitaOrigin: 'http://kavita', active: scope,
    listId: 9, listTitle: 'Example', description: '[libretto:example]', itemKeysListId: 9, itemKeys: new Map([[23, '7:11'], [25, '7:99']]) };
  const check = (path, body) => permits(new URL(path, 'http://kavita'), 'POST', body, state);
  assert(check('/api/ReadingList/update-by-chapter', { readingListId: 9, seriesId: 7, chapterId: 12 }));
  assert(check('/api/ReadingList/update-position', { readingListId: 9, readingListItemId: 23, fromPosition: 0, toPosition: 1 }));
  assert(check('/api/ReadingList/update', { readingListId: 9, title: 'Example', summary: '[libretto:example]', promoted: true }));
  assert(!check('/api/ReadingList/delete-item', { readingListId: 9, readingListItemId: 23 }));
  assert(!check('/api/ReadingList/update-by-series', { readingListId: 9, seriesId: 7 }));
  assert(!check('/api/ReadingList/update-by-chapter', { readingListId: 10, seriesId: 7, chapterId: 12 }));
  assert(!check('/api/ReadingList/update-position', { readingListId: 9, readingListItemId: 23, fromPosition: 0, toPosition: 0 }));
  assert(!check('/api/ReadingList/update-position', { readingListId: 9, readingListItemId: 23, fromPosition: 0, toPosition: 1, remove: true }));
  assert(!check('/api/ReadingList/update-position', { readingListId: 9, readingListItemId: 25, fromPosition: 2, toPosition: -1 }));
  assert(!permits(new URL('/api/apply', 'http://libretto'), 'POST', { scope: 'example' }, state));
  assert(!permits(new URL('/api', 'http://lazylibrarian'), 'GET', undefined, state));
  state.listId = 10;
  assert(!check('/api/ReadingList/update-position', { readingListId: 10, readingListItemId: 23, fromPosition: 0, toPosition: 1 }), 'prior-list item proof cannot authorize next scope');
});
test('recipe PUT boundary requires exact approved complete payload', () => {
  const state = { librettoOrigin: 'http://libretto', kavitaOrigin: 'http://kavita', saveRecipe: recipe };
  assert(permits(new URL('/api/recipes/example', 'http://libretto'), 'PUT', recipe, state));
  assert(!permits(new URL('/api/recipes/example', 'http://libretto'), 'PUT', { ...recipe, enabled: false }, state));
  assert(!permits(new URL('/api/recipes/other', 'http://libretto'), 'PUT', recipe, state));
});
test('raw native running/malformed/store drift refuses; installed scheduler nextRun must be after stage', () => {
  const raw = Buffer.from(JSON.stringify([{ id: 'run1', status: 'ok', finishedAt: '2026-10-09T15:00:00Z' }]));
  class Cron { constructor(expression, options) { assert.equal(options.paused, true); this.expression = expression; } nextRun() { return new Date(now + Number(this.expression)); } stop() {} }
  assertRunAdmission(raw, sha(raw), [recipe], Cron, 'UTC', now + 180_000, now);
  const scheduled = { ...recipe, variables: { ...recipe.variables, schedule: '180001' } };
  assertRunAdmission(raw, sha(raw), [scheduled], Cron, 'UTC', now + 180_000, now);
  assert.throws(() => assertRunAdmission(raw, sha(raw), [{ ...scheduled, variables: { ...scheduled.variables, schedule: '180000' } }], Cron, 'UTC', now + 180_000, now), /intersects/);
  const running = Buffer.from(JSON.stringify([{ id: 'run2', status: 'running' }]));
  assert.throws(() => assertRunAdmission(running, sha(running), [], Cron, 'UTC', now + 180_000, now), /running/);
  assert.throws(() => assertRunAdmission(Buffer.from('[]'), sha(raw), [], Cron, 'UTC', now + 180_000, now), /changed/);
  assert.throws(() => assertRunAdmission(Buffer.from('{}'), sha('{}'), [], Cron, 'UTC', now + 180_000, now), /malformed/);
});
