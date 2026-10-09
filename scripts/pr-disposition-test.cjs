// Exercise the actual workflow classifier with deterministic GitHub fixtures.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const workflow = fs.readFileSync(path.join(root, '.github/workflows/pr-disposition.yml'), 'utf8');
const script = workflow.split('          script: |\n')[1].split('\n').map(line => line.replace(/^            /, '')).join('\n');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const run = new AsyncFunction('github', 'context', 'core', script);
const renovate = vm.runInNewContext('(' + fs.readFileSync(path.join(root, '.renovate/autoMerge.json5'), 'utf8') + ')');

async function classify(file, type) {
  const updates = [];
  const pr = { number: 1, user: { login: 'renovate[bot]' }, labels: [{ name: `type/${type}` }],
    head: { ref: 'renovate/test-image', sha: 'a'.repeat(40) }, title: 'Update image', auto_merge: null };
  const rest = { pulls: { list() {}, listFiles() {} }, issues: { listComments() {},
    async removeLabel() {}, async addLabels(data) { updates.push(...data.labels); } } };
  const github = { rest, async paginate(method) {
    if (method === rest.pulls.list) return [pr];
    if (method === rest.pulls.listFiles) return [{ filename: file }];
    if (method === rest.issues.listComments) return [];
    throw new Error('Unexpected GitHub request');
  } };
  await run(github, { repo: { owner: 'test', repo: 'test' } }, { info() {}, warning() {}, error() {} });
  assert.equal(updates.length, 1);
  return updates[0];
}

(async () => {
  const cases = [
    ['kometateam/kometa', 'media/kometa/app/helmrelease.yaml'],
    ['linuxserver/lazylibrarian', 'downloads/lazylibrarian/app/helmrelease.yaml'],
    ['linuxserver/calibre', 'downloads/lazylibrarian/app/epub-convert-cronjob.yaml'],
  ];
  for (const [image, file] of cases) {
    const matching = renovate.packageRules.filter(rule => (rule.matchPackageNames || []).some(pattern =>
      pattern.startsWith('/') && new RegExp(pattern.slice(1, -1)).test(`docker.io/${image}`)));
    assert(matching.length > 0, `${image}: missing package carve-out`);
    assert.equal(matching.at(-1).automerge, false, `${image}: last matching package rule must hold`);
    const leaf = renovate.packageRules.findIndex(rule => (rule.matchFileNames || []).includes(`kubernetes/*/apps/${file.split('/')[0]}/**`) && rule.automerge);
    assert(leaf >= 0 && renovate.packageRules.indexOf(matching.at(-1)) > leaf, `${image}: carve-out must follow the leaf automerge rule`);
    for (const type of ['patch', 'minor', 'digest', 'major']) {
      assert.equal(await classify(`kubernetes/main/apps/${file}`, type), 'disposition:manual-only', `${image}: ${type}`);
    }
  }
  assert.equal(await classify('kubernetes/main/apps/media/tautulli/app/helmrelease.yaml', 'minor'), 'disposition:auto-merge');
  assert.equal(await classify('kubernetes/main/apps/downloads/qbittorrent/app/helmrelease.yaml', 'patch'), 'disposition:auto-merge');
  assert.equal(await classify('kubernetes/main/apps/downloads/qbittorrent/app/helmrelease.yaml', 'minor'), 'disposition:manual-only');
  assert.equal(await classify('kubernetes/main/apps/media/plex/app/helmrelease.yaml', 'patch'), 'disposition:shepherd');
  console.log('PR disposition carve-out parity: 16 cases passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
