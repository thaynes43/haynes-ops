#!/bin/bash
# SABnzbd post-processing script: extract archives SABnzbd left unextracted.
# https://github.com/thaynes43/haynes-ops/issues/3834
#
# SABnzbd (5.1.3, and the 5.2 betas) picks the archives to unpack by file
# extension. On an obfuscated post the files have no extension yet, and
# "Deobfuscate final filenames" adds `.7z` only AFTER the unpack step, so the
# job finishes as one unextracted `<random>.7z` and Sonarr stops at "Found
# archive file, might need to be extracted". SABnzbd runs this script after all
# of its own steps, so the extension is there by now.
#
# For every .7z / .7z.001 / .zip in the job folder: extract it in place, and
# delete it (with its volumes) only when 7-Zip exits cleanly. Anything that
# fails (password, missing volume, corrupt) is left exactly as it was.
# Always exits 0: the job's own result stands, so a missing 7-Zip binary or one
# bad extra archive can never fail a good download (should script_can_fail ever
# be turned on). The last line printed is what SABnzbd's history shows.
# Mounted read-only at /scripts from the `sabnzbd-scripts` ConfigMap (its own ks,
# ./ks.yaml); set as the script of the sonarrtv, radarrmovies and lidarr
# categories on both instances.
set -u

job_dir="$1"
pp_status="${7:-0}"

if [ "$pp_status" != "0" ]; then
  echo "Job did not finish cleanly (status $pp_status), nothing extracted"
  exit 0
fi
if [ ! -d "$job_dir" ]; then
  echo "No job folder, nothing extracted"
  exit 0
fi

if command -v 7zz >/dev/null 2>&1; then
  sevenzip=7zz
elif command -v 7z >/dev/null 2>&1; then
  sevenzip=7z
else
  echo "No 7-Zip binary found, nothing extracted"
  exit 0
fi

extracted=0
failed=0
while IFS= read -r -d '' archive; do
  dest="$(dirname "$archive")"
  echo "Extracting $archive"
  # stdin from /dev/null: an encrypted archive fails instead of waiting for a password.
  if "$sevenzip" x -y -aos -bd -o"$dest" "$archive" </dev/null; then
    case "$archive" in
      *.[0-9][0-9][0-9]) rm -f -- "${archive%.*}".[0-9][0-9][0-9] ;;
      *) rm -f -- "$archive" ;;
    esac
    extracted=$((extracted + 1))
  else
    echo "Could not extract $archive, left in place"
    failed=$((failed + 1))
  fi
done < <(find "$job_dir" -type f \( -iname '*.7z' -o -iname '*.7z.001' -o -iname '*.zip' \) -print0)

if [ "$failed" -gt 0 ]; then
  echo "Extracted $extracted archive(s), $failed could not be extracted"
  exit 0
fi
echo "Extracted $extracted archive(s)"
exit 0
