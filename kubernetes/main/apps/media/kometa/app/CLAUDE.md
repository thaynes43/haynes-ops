# Kometa collection labels are the haynesnetwork chip source of truth

The haynesnetwork app (`*.haynesnetwork.com` front door) turns each collection's
labels into the category filter chips on its Movies and TV Collections walls. The
app reads a collection's own Plex labels and derives ONE category from them, so
every collection definition in this Kometa config MUST end up with a deliberate
category — either declared inline with `label:` or knowingly relying on the
app-side derive (below). Categories are OPEN: a new `label:` string simply becomes
a new chip. There is NO "Other" bucket — an unlabeled collection just shows under
"All" and contributes no chip. Use these CORE definitions:

- **Universe** — an order-agnostic shared world umbrella-ing MULTIPLE sub-series
  (Wizarding World, MCU, Middle Earth, Monsterverse, DC, X-Men, Alien/Predator,
  A Song of Ice and Fire). Order does not matter; it groups several lines.
- **Sequels** — a SINGLE ORDERED film or show line (Harry Potter, Toy Story,
  Mission: Impossible, Game of Thrones). A trilogy is just a short Sequels line.
  If you are unsure between Universe and Sequels: one ordered line = Sequels;
  several lines sharing a world = Universe.
- **Director / Actor** — the people-file collections (`movies-people.yml`).
- **List** — charts, awards, seasonal, curated hand-picked lists.
- **Studio** — studio showcases (A24, Disney Animation, DreamWorks).
- **Audio** — audio-quality collections (Dolby Atmos, DTS X, Spatial Surround).
- Coin a new category only when none of these fit.

A collection carries EXACTLY ONE category label. A title may live in both a
Universe collection AND a Sequels collection — that is membership overlap, never a
second category label on one collection.

## Rules

1. **Use `label:` (APPEND). NEVER `label.sync` or `label.remove`.** `label.sync`
   replaces the whole label set and would strip the managed `Kometa` label the
   haynesnetwork app reads for provenance. `label:` adds our category alongside it;
   it is idempotent, so the daily 06:30 collections run re-appends as a no-op.
2. **Hand-authored definitions declare `label:` inline** — on the `templates:`
   block where a file's collections all share one category (e.g. `movies-people.yml`
   Director/Actor; `movies-franchises.yml` Movies → Sequels), otherwise per
   collection (e.g. `shows-franchises.yml`, where the Shows template spans both
   Universe and Sequels).
3. **Default-produced collections cannot take a `label:` template variable.** Do
   NOT try a same-name `blank_collection` companion for a collection the Default
   builds in the SAME run — Kometa skips it as a duplicate and the label is never
   applied (proven by the 2026-07-17 dry-run). Instead the haynesnetwork app
   derives their category from the section labels Kometa already applies:
   `TMDb Collections` → Sequels, `Universe Collections` → Universe,
   `Oscars Winners Awards` / `Golden Globes Awards` → List, and the legacy TV
   `Show Franchise Collections` → Universe. So a franchise/universe/award Default
   collection is category-correct with NO Kometa change (it "knowingly relies on
   the derive"). An inline owner `label:` always WINS over this fallback map.
4. **`blank_collection: true` + `label:` companions are ONLY for ORPHANS** — a
   collection that carries a `Kometa` label but is NOT built by any Default in the
   run (e.g. the movie "Star Wars", the four legacy TV orphans). Those are safe to
   append to and live in `movies-star-wars-labels.yml` / `shows-default-labels.yml`,
   loaded LAST so the target already exists.
5. **To relabel a Default's STATIC keyed collection, turn it off and re-author it —
   and load the custom file BEFORE the Default.** Set `use_<key>: false` on the
   Default (e.g. `use_best_picture: false` on the oscars/golden Defaults,
   config.yml) and re-create it as a custom def with an inline `label:` (see
   `movies-awards.yml`). Kometa registers collection NAMES at config-parse time and
   duplicate-skips later same-name definitions even when the Default's copy is
   toggled off (run-proven 2026-07-17), so the custom file must sit ABOVE the
   Default in `collection_files` — first definition wins. Preserve the Default's
   acquisition settings on the custom so no members are lost on the swap.
6. **config.yml is PVC-seeded, collection files are live from git.** Editing a
   collection/overlay file under `config/` takes effect on the next run (they mount
   from the `kometa-config-files` ConfigMap at `/config/git`). Editing config.yml
   in `externalsecret.yaml` (e.g. a Default toggle) does NOT — the init container
   only seeds `/config/config.yml` when it is ABSENT. To apply a config.yml change,
   force a re-seed by deleting `/config/config.yml` on the `kometa` PVC before the
   next run.
7. **`collection_order` must be a NATIVE Plex sort (`release` / `alpha` / `custom`
   for list-order) on anything large.** Non-native sorts (`release.desc`, any
   plex_search-style sort) make Kometa enforce the order itself with one Plex move
   API call per item, logged as "(N/total) Moving X after Y". The per-move cost
   grows with collection size (~4s at 247 items, ~13s at 1,374), so a big
   collection can add HOURS to a run — the 2026-07-25 collections run spent 7.5 of
   its 8.3 hours reordering Spatial Surround + Dolby Atmos. Full doctrine comment
   in `config/movies-collections.yml`. Watch runs for "Moving" lines (Loki:
   `{namespace="media", pod=~"kometa-.*"} |= "Moving "`) — a spike means a
   non-native order snuck back in or a list order cascaded.
