# archive/

Frozen / inactive content retained for reference and extractability.

Subdirectories here are **excluded from Renovate** (`archive/**` is in
`.github/renovate.json5` `ignorePaths`) and are not reconciled by Flux. Nothing
here receives automated dependency updates.

- `alumni/` — SIGO alumni org infrastructure (GCP/Terraform + docker-compose).
  Still extractable as a unit (`git filter-repo --path archive/alumni/`). See
  `alumni/README.md` for ownership boundary.
