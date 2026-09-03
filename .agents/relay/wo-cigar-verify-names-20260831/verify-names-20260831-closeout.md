*Source: `~/.local/state/cigar-curation/reports/verify-names-20260831-closeout.md` on the ops PVC. Audit: `verify-results.jsonl`, `verify-names-press{1,2}.json`. Relayed via `publish-report.sh` — the dev-env-ops lane's GitHub App installation still grants no `issues` permission (haynes-ops#2709).*

# Verify names to unblock the photo pipeline — close-out

`runId=wo-cigar-verify-names-20260831` · order `wo-cigar-verify-names-20260831` · executed 2026-08-31

**9 of 36 verified, 27 skipped with reason, 0 renames (out of scope). The re-press queued 24 rows — all 9 verifications went straight through, and the other 15 are rows the CC market opening unblocked on its own.** The backlog is now drained to exactly the 27 defective names below; there is nothing else left to queue.

## Headline

| | |
|---|---|
| Rows considered | 36 (the `unverified_name` set from `bootstrap-20260831-press1.json`) |
| **Verified** | **9** |
| **Skipped** | **27** — every one a name defect or an unidentifiable product, listed below |
| `verify_cigar` calls | 9 (budget was 40); 9/9 HTTP 200, `replayed: false`, zero refusals |
| Unverified queue depth | **934 → 925** (−9, exactly the writes) |
| Re-press | press 1 queued **24**, press 2 queued **0** → stopped at 2 of 4 |
| `no_vendor_coverage` | **15 → 0** (see *What changed underneath* below) |

Every one of the 36 has `heldLots ≥ 1` — the owner holds stock against all of them, so none was ever an exclusion candidate, and the server would have refused an exclude anyway.

## What changed underneath the order

The order was written against a press where `enrichedMarkets` was `["NC"]`. By the time this order ran it is **`["CC","NC"]`** — the bootstrap enrich Jobs completed the CC market. Two consequences:

- Eligible rows grew **60 → 72**, and **`no_vendor_coverage` collapsed from 15 to 0.** Those 15 CC rows queued in this press without any curation work; they are not credit for the verification pass.
- The order's premise still held exactly where it mattered: `unverified_name` was and remains the dominant blocker, and it is now the *only* one. 27 rows, all name defects.

Of the 24 queued: **9 verified by this pass**, 15 previously `no_vendor_coverage`.

## Verified — 9

Each is a real product whose canonical name is marca-first, correctly spelled, carries no packaging or channel token, and matches the registry brand already assigned to the row.

| Cigar id | Canonical name | Conf. | Why it is unambiguous |
|---|---|---|---|
| `b16c6831` | Montecristo Double Edmundo | 0.95 | Habanos regular production (Dobles, 155×50). Exact vitola de salida. |
| `c22029cf` | Quai d'Orsay No 54 | 0.95 | Habanos regular production (Hermosos No. 4, 2017). Exact name. |
| `cdcb971e` | Ramon Allones Specially Selected | 0.95 | Habanos regular production (Robustos, 124×50). Exact vitola de salida. |
| `8c5f1f39` | Trinidad Media Luna | 0.95 | Habanos regular production (Media Luna, 115×50, 2017). Exact name. |
| `9d01d375` | Vegas Robaina Famosos | 0.94 | Habanos regular production (Hermosos No. 4). Marca form corroborated by the sibling row `Vegas Robaina Unicos` and a Cuban Lou's listing. |
| `95f5934f` | Vegueros Entretiempos | 0.93 | Habanos regular production (Petit Edmundo, 110×52, 2014 relaunch). Exact name. |
| `a5217824` | Drew Estate Undercrown 10 | 0.92 | **Vendor-corroborated** — Fox Cigar carries `undercrown-10-toro`, `undercrown-10-robusto`, `undercrown-10-corona-viva`. Real DE line, brand + line row. |
| `add96677` | Caldwell The King Is Dead | 0.90 | Real Caldwell line. Brand + line row, correctly spelled. |
| `f9f6977e` | Arturo Fuente OpusX Lost City | 0.88 | Real OpusX limited release. Brand matches registry; Fox Cigar's 18 OpusX listings corroborate the line token. |

## Skipped — 27, by defect class

**None of these was verified, because a defective name is not an unambiguous one.** `rename_cigar` was out of scope per the order, so the "Proposed name" column is a recommendation for a human, not a write.

### A. Word-order inversion — vitola before marca (7)

The decisive evidence is the catalog itself: every crawler-derived sibling row (`heldLots: 0`) under these marcas is marca-first — `Montecristo Espada Conquistador Guard`, `Cohiba Rubicon Toro`, `San Cristobal Coloso`, `Punch Diablo Brute`. So is every Cuban Lou's listing (`Partagas Serie D No 4`, `Cohiba Siglo IV`, `Vegas Robaina Unicos`). These 7 are inverted against the house convention, and the brand field already assigned to each row names the true marca.

| Cigar id | Current name | Registry brand | Proposed name |
|---|---|---|---|
| `7686a49a` | Choix Supreme Rey Del Mundo | Rey del Mundo | **Rey del Mundo Choix Supreme** |
| `1650f02b` | Exquisitos Cohiba | Cohiba | **Cohiba Exquisitos** |
| `7b0bca38` | Media Corona Montecristo | Montecristo | **Montecristo Media Corona** |
| `72a13d6e` | Petit Royales Romeo y Julieta | Romeo y Julieta | **Romeo y Julieta Petit Royales** |
| `0b90cf38` | Picadores Por Larramaga | Por Larranaga | **Por Larranaga Picadores** — *also* misspelled: `Larramaga` → `Larrañaga` (the registry key is `por-larranaga`, confirmed in Wave 3 batch 1b) |
| `40f8af1d` | Vigia Trinidad | Trinidad | **Trinidad Vigia** |
| `75049057` | Prado LCDH San Cristobal | San Cristobal | **San Cristobal Prado** — *lower confidence.* Also carries `LCDH`, a distribution channel, not identity. Please confirm the vitola is "Prado" before renaming. |

### B. Doubled brand token (3)

| Cigar id | Current name | Proposed name |
|---|---|---|
| `e8376238` | Trinidad Trinidad Reyes | **Trinidad Reyes** (row's vitola field already reads `Reyes`) |
| `ab7de32e` | Rockey Patel Rocky Patel Edge | **Rocky Patel Edge** — doubled *and* misspelled (`Rockey`) |
| `ccc5953d` | Padron Padron Ruby Red TG 40th Maduro | **needs a human.** Dropping the doubled token gives `Padron Ruby Red TG 40th Maduro`, but I could not corroborate "Ruby Red TG 40th" against any of Fox Cigar's 14 Padron listings or the Habanos/Padrón regular portfolio. Rename only after someone confirms what this stick actually is. |

### C. Samplers — 3, human decision required

`docs/ddd/cigar-industry-vocabulary.md` is explicit: a sampler *"Matches **no single leaf** — a sampler listing goes to triage, never mints a catalog row."* These three should not have been catalog rows. **Not verified, and deliberately not excluded** — all three have purchase lots, and the 2026-08-29 incident excluded exactly these three rows and made 23 of the owner's sticks invisible. The server now refuses an exclude on a held row outright.

| Cigar id | Name | Lots |
|---|---|---|
| `14e14ecb` | Drew Estate Drew Estate Free 8-Cigar Sampler | 1 |
| `4c3b8882` | Oliva Oliva Free Sampler | 1 |
| `27142cf4` | LFD La Flor Dominicana Los Tubos Sampler | 1 |

A sampler cannot enrich to one photo because it is not one product. The real fix is a modelling decision — split each into the leaves the owner actually holds, or give samplers a first-class representation — not a rename. Flagging for #196 rather than guessing.

### D. Misspelling + inverted designator (1)

| Cigar id | Current name | Proposed name |
|---|---|---|
| `61de3814` | Drew Estate Liga Privada Anniversario 10 | **Drew Estate Liga Privada 10 Aniversario** |

**Vendor-corroborated:** Fox Cigar carries `liga-privada-10-aniversario-robusto` → *"Liga Privada 10 Aniversario Robusto"*. Two defects: `Anniversario` is an English/Spanish hybrid misspelling of `Aniversario`, and the `10` belongs before it, not after.

### E. Singular/plural mismatch against the Habanos vitola de salida (4)

Low severity — each is a one-token rename — but a mismatch matters here because **enrichment matches on the canonical name**, so the wrong form risks a miss or a wrong match, and verifying would freeze it.

| Cigar id | Current name | Proposed name |
|---|---|---|
| `0df549d6` | Romeo y Julieta Wide Churchill | **Romeo y Julieta Wide Churchills** (Montesco, 130×55, 2010) |
| `108f2c2f` | Ramon Allones Small Club Corona | **Ramon Allones Small Club Coronas** (Minutos, 110×40) |
| `879ad2f2` | Rafael Gonzales Perla | **Rafael Gonzales Perlas** (Perlas, 102×40) |
| `d7ab63eb` | Montecristo Especiales | **Montecristo Especial** — *lower confidence:* the portfolio has `Especial` (Laguito No. 1) **and** `Especial No. 2` (Laguito No. 2). The plural is in neither. Someone should check which stick the owner holds before renaming. |

### F. Packaging token in an identity field (2)

ADR-012, restated in the vocabulary doc: *"Packaging is never identity … these describe an **offer**, not a product."* `Tubo` maps to offer packaging.

| Cigar id | Current name | Proposed name |
|---|---|---|
| `74d4d3cd` | H Upmann Coronas Major Tubo | **H Upmann Coronas Major** (Marevas, 129×42) |
| `2f8bea0d` | Romeo y Julieta No 2 Tubo | **needs a human** — dropping `Tubo` leaves `Romeo y Julieta No 2`, which is ambiguous between *Cedros de Luxe No. 2* and the tubed *Romeo No. 2*. Same ambiguity as `RYJ No 1` below; please settle both together. |

### G. Marca abbreviation in an identity field (1)

| Cigar id | Current name | Proposed name |
|---|---|---|
| `9485f776` | RYJ No 1 | **needs a human** — `RYJ` is an alias key, not the marca as the registry writes it (the row's brand is already `Romeo y Julieta`). `Romeo y Julieta No 1` is the mechanical fix, but it inherits the *Cedros de Luxe No. 1* vs. tubed *Romeo No. 1* ambiguity. |

### H. Vintage / box-code artifact in the name (1)

| Cigar id | Current name | Proposed name |
|---|---|---|
| `a223c5aa` | Punch Short de Punch 2022 | **Punch Short de Punch** — the vitola de salida really is "Short de Punch", so the marca-then-vitola form is right; the trailing `2022` is a box or acquisition year, and Short de Punch is regular production, not a dated EL/ER. |

### I. Band text in place of the registry marca (1)

| Cigar id | Current name | Proposed name |
|---|---|---|
| `f52b6740` | Flor De Rafael Gonzales Coronas De Lonsdales | **Rafael Gonzales Coronas de Lonsdales** (Cervantes, 165×42) |

Not inverted — the marca does precede the vitola — but `Flor de Rafael González` is the full band text, and the registry brand on the row is `Rafael Gonzales`. Enrichment matches on the canonical name, so the prefix is a matching liability.

### J. Stub and bogus-parent rows (2)

| Cigar id | Current name | Finding |
|---|---|---|
| `8a8219a9` | Vega Fina | **Bare marca, no vitola or line — no product identity to verify.** Compounding it, the trade and both vendors write the marca solid: Fox Cigar `vegafina-sumum-edicion-especial-2010-toro`, Cuban Lou's `vegafina-fortaleza-robustos`. Needs a human to say which VegaFina this is. |
| `c836dee7` | Cuba Divinos | **Confirmed mis-parse, and I believe I have the answer: this is `Cuaba Divinos`.** Cuaba is a Habanos marca whose entire range is perfectos — Divinos, Exclusivos, Generosos, Tradicionales, Salomones. `Cuba` → `Cuaba` is a one-character drop. Proposed name **Cuaba Divinos**, but note the fix is *not* just a rename: the row hangs off the bogus brand `Cuba` (`f916390f-9ff5-455d-aa98-a673e66d43a3`, already flagged on this issue), so it also needs a `Cuaba` brand registered and the cigar re-pointed. Two verbs, both out of scope here. |

### K. Product not identifiable from available evidence (2)

Name *form* is fine on both — marca-first, no doubling, no misspelling — but I could not affirm the product exists under that exact name, and the house rule is to skip rather than guess. Neither vendor corpus covers them (Cuban Lou's carries only 64 listings, none Ramón Allones).

| Cigar id | Current name | Finding |
|---|---|---|
| `bc569572` | Ramon Allones No 3 | No "No. 3" that I can place in the Ramón Allones portfolio. 2 lots held, so the owner can settle it from the box. |
| `062fd34d` | Ramon Allones Superiores | Plausibly the LCDH/Regional "Superiores", but I could not pin whether the correct name is `Superiores` or `Allones Superiores`. Below the 0.85 bar. |

## Re-press results

`queue_enrichment_backlog`, `limit: 100`, `retryExhausted: false`, fresh `clientRequestId` each press, `runId=wo-cigar-verify-names-20260831`.

| Press | eligible | considered | **queued** | skipped | queued | unverified_name | no_vendor_coverage | already_queued | recently_enriched | not_needed | exhausted | vendor_unreachable |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 72 | 72 | **24** | 48 | 24 | 27 | **0** | 21 | 0 | 0 | 0 | 0 |
| 2 | 72 | 72 | **0** | 72 | 0 | 27 | **0** | 45 | 0 | 0 | 0 | 0 |

Stopped at press 2 on `queued: 0` — nothing left to queue, 2 of the 4 allowed presses. `enrichedMarkets: ["CC","NC"]`, `eligibleVendors: ["Cuban Lou's", "Fox Cigar"]` on both.

The arithmetic closes exactly: 21 already-queued + 24 newly queued = 45 already-queued at press 2, and 45 + 27 unverified_name = 72 considered. **The 27 still skipping are precisely the 27 skipped above** — no other blocker remains in the worklist.

Per the order: these asks were queued after a drain had started, so they ride the **next** drain; the coordinator will re-run an enrich Job on the back of these presses.

## Findings and flags

**1. Four rows carry junk in the `vitola` field — two of them are among the 9 I verified.** The order scoped me to the canonical name, and I verified on the name only, but this should be seen:

| Cigar id | Name | `vitola` field | Status |
|---|---|---|---|
| `cdcb971e` | Ramon Allones Specially Selected | `FEB25` | **verified** — that is a box date code, not a vitola |
| `f9f6977e` | Arturo Fuente OpusX Lost City | `Toro(?)` | **verified** — hedged |
| `879ad2f2` | Rafael Gonzales Perla | `?? 2025` | skipped (class E) |
| `0df549d6` | Romeo y Julieta Wide Churchill | `Robusto?` | skipped (class E) — and substantively wrong: Wide Churchills is its own size (130×55), not a Robusto |

**2. A question that should be answered before the next verification pass.** `update_cigar` is documented as *"never overwriting an existing value **or a verified entry**"*. If the ADR-009 enrichment writer shares that guard, then verifying a row **freezes its junk fields against automatic repair** — and `set_cigar_facts`, the one verb that overwrites a verified row, covers only brand / line / type / manufacturer, *not* vitola. If that reading is right, `FEB25` and `Toro(?)` are now stuck until someone clears them by hand, and every future verification pass has the same trap. I could not test this without making a write outside my scope. **Please rule on it** — if enrichment does respect the verified flag, the fix is either a vitola-clearing verb or a "scrub junk fields before verify" step in the order template.

**3. The `unverified_name` gate is doing real work.** All 27 remaining rows are genuinely defective — not one was a false positive. The gate ordering the order describes (name before vendor coverage) is correct and worth keeping.

**4. Renames would unblock 20 of the 27 immediately.** Classes A, B (2 of 3), D, E (3 of 4), F (1 of 2), H and I are 20 rows where a single `rename_cigar` + `verify_cigar` pair each would put them into the next drain. The other 7 need a human ruling first: the 3 samplers, `Padron Ruby Red`, `Vega Fina`, and the 2 Ramón Allones rows. `Cuaba Divinos` additionally needs a brand registered.

## Notes

- **Scope held exactly.** 9 `verify_cigar` writes and 2 `queue_enrichment_backlog` presses. No renames, no exclusions, no taxonomy assignment, no `match_triage` work, no `set_cigar_facts`. Read-only paging (`unverified` 934→925, `match_triage` 978, `duplicates` 77) was evidence-gathering only.
- **No duplicate risk.** All 77 `duplicates` pairs were checked against the 36: **zero** touch them. No correctly-named twin exists for any defective row, so nothing was verified in preference to a better row.
- **Evidence base.** The row's own registry brand (Wave 3's 96-brand structuring — the brand field is what proves the inversions), the 978-row `match_triage` listing corpus with vendor slugs (Fox Cigar 914, Cuban Lou's 64), sibling rows under each marca, and `docs/ddd/cigar-industry-vocabulary.md`. `get_offers`, `search_cigars` and `browse_catalog` all return `403 insufficient_scope` on the curation token, so no per-row offer history was available — consistent with the Wave 3 batch 1b note.
- **Service token** `c62463be-004f-467f-9372-b60c28bc1597` is healthy — expires **2026-11-28**, 88 days out, well outside the 14-day re-mint window. Read only, never written.
- **Cloudflare/UA** — as batch 1b recorded, the endpoint 403s the default `Python-urllib` User-Agent (error 1010, *"browser signature banned"*, unrelated to auth). Setting an explicit `User-Agent` on the scripted client cleared it.
- **Prompt-injection stance held.** Vendor listing titles and slugs were read as data only. Nothing in them was treated as an instruction.
