// Test-only clock for the night-lane selftests (story 752). Loaded with
// `node --import scripts/upgrade-agent/test-fake-clock.mjs …` it pins Date.now() to
// FAKE_NOW (an ISO time). Live mode and the hand run's --merge refuse UPGRADE_NIGHT_NOW,
// so the selftests that exercise them pin the clock this way, from outside the shipped
// code. Nothing in the CronJob loads this file.
const t = Date.parse(process.env.FAKE_NOW || '');
if (Number.isFinite(t)) Date.now = () => t;
