# Plex recommendations for tonight

When Tom asks what to watch, refresh his owner-account history and current Plex
library with the collector, then return a short, personalized list in three groups:
movies he has not watched, shows he has not started, and shows to resume.

## Refresh

From a haynes-ops task worktree in the dev-env pod:

```bash
scripts/plex-tonight/collect.sh
```

The snapshot is written privately outside git at
`~/.cache/plex-tonight/snapshot.json`. Read the timestamp and per-source coverage
before using it. Refresh for each new request; a prior shortlist is not evidence of
current availability or watch state. The script collects evidence; the agent
chooses and explains recommendations.

Credentials stay inside the running `frontend/haynesnetwork-main` app container.
The collector executes read-only API requests there using the environment populated
by [haynesnetwork's ExternalSecret](../../kubernetes/main/apps/frontend/haynesnetwork/app/externalsecret.yaml).
Do not dump environment variables, request URLs containing API keys, or Kubernetes
Secrets. Do not copy credentials into the dev-env pod, source files, or output.

## Interpret the evidence

- Resolve the owner from Tautulli's admin account, consistently across sources.
  Do not mix other household or shared-library accounts into his history.
- Include all available history pages from HaynesTower, HaynesOps, and HaynesKube.
  HaynesTower holds older history; HaynesOps holds recent viewing. Keep source
  provenance and distinguish playback sessions from distinct watched titles.
- Use the Movies and TV Shows libraries on HaynesOps and HaynesTower. Their
  catalogs mirror shared media. Skip home videos, photos, exercise, music, and
  YouTube archives. Deduplicate titles using shared GUIDs, retaining both server
  locations; do not merge remakes on title alone.
- Combine Tautulli completion evidence with the owner's Plex watched marks on
  both servers. History may begin after a title was watched. Missing history is
  not proof that a title is new to him. Brief or zero-progress starts should not
  become positive taste signals.
- For a continuation, inspect regular episodes, their watched state, any playback
  offset, and actual files. Exclude specials from the normal sequence. Explain
  gaps rather than treating the first unrecorded early episode as the next one:
  an entire later season may already have been watched. Distinguish caught up
  with available episodes from having finished a series.
- A library entry establishes catalog presence. Before calling a final pick
  available tonight, verify its movie file or first/next episode with Plex
  metadata and `checkFiles=1`. Report inaccessible or unverified files honestly.
- Use completed recent adult viewing and sustained series viewing to infer taste.
  The owner account also contains family viewing. Do not let repeated children's
  playback dominate an adult evening recommendation. Treat these as inferences,
  not explicit likes. Plex `userRating` is populated from IMDb by Kometa's
  `mass_user_rating_update: imdb` in its ExternalSecret template; it is not a
  personal rating.

## Deliver the shortlist

Give roughly three choices per group, with one clear best pick. Explain each fit
using an actual watched title or pattern. Include movie runtime, a show's first
episode runtime, and an exact next episode for continuations. Cite the live
snapshot for personal evidence and check current public primary sources for
premise/release facts. Keep descriptions free of plot spoilers.

Use credential-free Plex links to make the list actionable:

```text
https://app.plex.tv/desktop/#!/server/{machineIdentifier}/details?key=%2Flibrary%2Fmetadata%2F{ratingKey}
```

For a chosen HaynesOps movie or episode, verify the file without starting playback
(replace `RATING_KEY` with its key from the snapshot):

```bash
kubectl exec -i -n frontend deploy/haynesnetwork-main -c app \
  -- node --input-type=module - RATING_KEY <<'JS'
const key = process.argv[2];
if (!/^\d+$/.test(key)) throw new Error('A numeric rating key is required');
try {
  const response = await fetch(
    `http://plexops.media.svc.cluster.local:32400/library/metadata/${key}?checkFiles=1`,
    {
      headers: { Accept: 'application/json', 'X-Plex-Token': process.env.PLEX_HAYNESOPS_TOKEN },
      redirect: 'error',
      signal: AbortSignal.timeout(15000),
    },
  );
  if (!response.ok) throw new Error('Metadata request failed');
  const item = (await response.json()).MediaContainer.Metadata?.[0];
  if (!item) throw new Error('Metadata is missing');
  console.log(JSON.stringify({
    title: item.title, ratingKey: item.ratingKey, duration: item.duration,
    viewCount: item.viewCount ?? 0, viewOffset: item.viewOffset ?? 0,
    parts: (item.Media ?? []).flatMap(media => media.Part ?? [])
      .map(part => ({ exists: part.exists, accessible: part.accessible })),
  }));
} catch {
  console.error('Plex file verification failed');
  process.exitCode = 1;
}
JS
```

`checkFiles=1` returns `Part.exists` on the deployed Plex version. If a stronger
readability check is necessary, a one-byte Range GET to the returned Part key with
`?download=1` should return 206; immediately cancel the body if the server ignores
the Range header. Never print the file path or token. Omitting `download=1` can
produce an HTTP 500 for a readable file on this deployment.

Keep personalized reports and watch data outside the repository. Never commit
snapshots, account records, client addresses, device details, or secrets. A user
can repeat the request simply by saying "Plex tonight"; this runbook and collector
provide the starting point. No downloads, watch-state changes, or service
reconfiguration are needed for recommendations.
