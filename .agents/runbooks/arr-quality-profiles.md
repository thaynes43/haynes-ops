# Sonarr / Radarr quality profiles and delay profile

Where each setting lives, what it is set to and why, and how to revert it. The owner
rulings are dated. Every arr setting below is in git (Recyclarr) **except the Sonarr
delay profile**, which Recyclarr 8.x cannot manage and is set through the API.

## Managed by Recyclarr (git)

`kubernetes/main/apps/media/recyclarr/app/recyclarr.yml`, synced nightly at 05:30 ET by
the `media/recyclarr` CronJob. A change made in the arr UI is reverted on the next run.
To apply a merged change at once:

```bash
flux reconcile kustomization -n media recyclarr --with-source
kubectl create job -n media --from=cronjob/recyclarr recyclarr-manual-$(date +%s)
```

| Setting (both `FHD-UHD` profiles) | Value | Ruling |
|---|---|---|
| Upgrade until quality | Radarr `Remux-2160p`, Sonarr `Bluray-2160p Remux` | upgrade-until-best, 2026-07-03 |
| Upgrade until score | Radarr 8450, Sonarr 500 (both were 10000) | reachable cutoff, 2026-10-03 |
| Minimum upgrade increment | 100 (was 1) | +100 minimum, 2026-10-03 |
| Radarr `Language: Not Original` CF (TRaSH) | -10000; the profile language also stays `Original` (set in the app) | "I don't want shit dubs I want films in their original language", 2026-10-03 |
| Sonarr `Language: Not English or Original` CF (local, `custom-formats/`) | -10000: neither English nor the show's original language is refused (a German dub of an English show); English dubs and original-language releases (anime in Japanese) stay allowed | language ruling, 2026-10-03 |

The score cutoff only applies on the top-quality rung. Any lower-quality file counts as
below cutoff, so on the lower rungs the +100 increment is what stops same-quality churn.

`min_format_score: 0` makes the two language CFs reject new matching grabs. The files
already on disk that matched when they were turned on (84 Radarr movies, 223 Sonarr
episodes) score -10000 and are replaced only when RSS sees a release in the right
language. The owner accepted that. Never start a search to speed it up.

## Sonarr delay profile (API only)

Sonarr, the default delay profile (id 1, no tags, applies to every series). Set
2026-10-03, ruling "Wait 2 hours".

| Field | Old | New |
|---|---|---|
| `usenetDelay` (minutes) | 0 | **120** |
| `torrentDelay` | 0 | 0 (unchanged: Sonarr has no torrent indexers, so Usenet is the only grab path) |
| `preferredProtocol` | usenet | usenet |
| `bypassIfHighestQuality` | true | true |
| `bypassIfAboveCustomFormatScore` | false | **true** |
| `minimumCustomFormatScore` (the bypass score) | 0 | **1775** |

Why 1775: the best a WEB-DL-1080p release can score without a repack is 1850 (WEB
Tier 01 + streaming-service tag + HD Streaming Boost). Under the +100 increment, a
release scoring 1751 or more cannot be beaten at the same quality. 1775 (WEB Tier 01 +
service tag) is the lowest score that actually occurs in that range. Such a first release
is grabbed at once. Anything lower waits up to 2 hours for a better one. In the 90 days
to 2026-10-03, 4,698 of 16,264 first RSS grabs scored 1775 or more. A user-invoked
search is never delayed.

While a release is held it shows in the Sonarr queue with status `delay`.

The setting lives only in Sonarr's database (backed up by VolSync). A fresh Sonarr
install resets it to 0 / false / 0, so re-apply it after a rebuild. The values in this
section are the ones to restore. To read it, or to re-apply it from the dev-env pod:

```bash
# read
kubectl exec -n media deploy/sonarr -c app -- sh -c 'curl -s -H "X-Api-Key: $SONARR__AUTH__APIKEY" \
  "http://localhost:$SONARR__SERVER__PORT/api/v3/delayprofile/1"'
# re-apply (or revert: usenetDelay 0, bypassIfAboveCustomFormatScore false, minimumCustomFormatScore 0)
kubectl exec -i -n media deploy/sonarr -c app -- sh -c 'curl -s -X PUT -H "X-Api-Key: $SONARR__AUTH__APIKEY" \
  -H "Content-Type: application/json" --data-binary @- \
  "http://localhost:$SONARR__SERVER__PORT/api/v3/delayprofile/1"' <<'JSON'
{"id": 1, "enableUsenet": true, "enableTorrent": true, "preferredProtocol": "usenet",
 "usenetDelay": 120, "torrentDelay": 0, "bypassIfHighestQuality": true,
 "bypassIfAboveCustomFormatScore": true, "minimumCustomFormatScore": 1775,
 "order": 2147483647, "tags": []}
JSON
```

Radarr's delay profile is untouched (0 / 0).
