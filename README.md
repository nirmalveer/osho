# OshoWorld podcast feeds

One RSS 2.0 subscription per Osho discourse series, with direct OshoWorld MP3
enclosures. Audio is streamed from the source; no audio is downloaded or rehosted.
This extends the English/Hindi feed layout and OPML format of this repository.
The original repository contained generated XML/OPML but no Python source in its
available Git history; `scripts/oshoworld.py` makes regeneration reproducible.

## Verified catalog: 8 September 2026

| Language | Nonempty series / feeds | Verified episodes |
| --- | ---: | ---: |
| English | 175 | 3,059 |
| Hindi | 176 | 2,462 |
| Total | 351 | 5,521 |

**Tao Upanishad has 127 verified MP3 files**, including 54, 83, and 122.
The earlier local transcript index was a different dataset and is not used here.

All 5,521 unique URLs returned HTTP 206, a recognizable MP3/ID3 signature and a
positive total byte length. All supported byte ranges. Verification retrieves
only the first 4 KiB, checks existence and seeking support, and does not assert
that an entire recording is uncorrupted. Some source responses use `text/plain`;
RSS enclosures correctly use `audio/mpeg` based on the file signature.

The public catalog has 352 entries, including an empty `audio-series-geeta-darshan-all`
entry. Its actual track list and series API both return zero, so no empty feed is
created. The individual Geeta Darshan series are included normally. Yoga Volume 1
has 11 database records, but two point to the same track-08 MP3: the feed contains
10 unique files. Track 08 also has a repaired URL in a different directory and a
duplicate source index; its published slug restores the correct 01–10 order.

These are counts of the live published catalog and verified URLs, not title ranges
or an inventory of unlinked server files. The current site's sitemap exposes
English and Hindi audio catalogs; no additional language catalog was found.

## Import into AntennaPod after publishing

Download one OPML file and import it using **Add Podcast → Import OPML**:

- [All 351 series](https://nirmalveer.github.io/osho/Osho_All_Podcasts.opml)
- [176 Hindi series](https://nirmalveer.github.io/osho/Osho_Hindi_Podcasts.opml)
- [175 English series](https://nirmalveer.github.io/osho/Osho_English_Podcasts.opml)
- [Tao Upanishad RSS](https://nirmalveer.github.io/osho/Hindi/Tao%20Upanishad.xml)

All `xmlUrl` and RSS self links already use `https://nirmalveer.github.io/osho/`.
The newly generated feeds become available there only after this commit reaches
`main` and GitHub Pages finishes deploying.

Set episode sorting to publication date, oldest first, for track 1 onward.
Dates are deliberately synthetic: track 1 is 2 January 2020, with one day per
subsequent track. They are ordering keys, not recording dates. Resume position
is stored by the podcast player, not encoded in RSS. Device playback/resume has
not been tested in an AntennaPod installation during this migration.

For existing subscribers, 136 ASCII feed paths and 1,861 matching episode GUIDs
are retained. Some retained GUID strings mention Archive.org solely as stable
identifiers; **no enclosure or playlist streams from Archive.org**. Other legacy
feeds have been replaced by the current series structure. Reimport the new OPML
for those subscriptions; cross-subscription playback history is not migrated.
The exact removed paths are in `catalog/legacy-map.json`.

## Regenerate

Use Python 3.10+ and install `requirements.txt`, then run:

```sh
python -m pip install -r requirements.txt
python scripts/oshoworld.py discover
python scripts/oshoworld.py verify
python scripts/oshoworld.py build
python -m unittest discover -s tests -v
python scripts/oshoworld.py package
```

On a machine with `uv`, each command can instead use
`uv run --with requests python scripts/oshoworld.py <command>`.

Discovery reads `/sitemap.xml` to find language landing pages, then requests
`/api/audio/catalog/{language}` and `/api/audio/get-all-audios-list/{series_id}`.
The latter returns the actual full track list, including volume splits and gaps;
it does not need inferred filenames, guessed counts, pagination limits, or a
hardcoded Next.js build ID. The series ID is checked on every returned track.
Raw and already-percent-encoded audio paths are normalized exactly once.

`verify` checks every distinct audio URL with bounded requests and retries.
`verify --retry-failed` resumes a checkpoint and retains previous successes with
their original verification timestamps. A plain `verify` rechecks everything.
`--workers` defaults to 6 and is capped at 12. Unresolved HTTP failures are saved
with their errors. `build` refuses untested URLs and excludes failed URLs with
an explicit per-series report; it never pads a feed to a claimed count.

`build` and `validate` work offline from the saved snapshots. Source indexes
determine track order when unique; published slugs provide natural volume/track
ordering when indexes restart or collide. Stable UUID GUIDs use source record
IDs for new episodes. Dates retain gaps if a failed recording is omitted.

## Files and checks

- `English/`, `Hindi/`: RSS 2.0 feeds with real byte lengths and UTF-8 titles.
- `playlists/`: corresponding UTF-8 M3U playlists with the same ordered URLs.
- `catalog/oshoworld.json`: discovered titles, IDs, counts, actual track URLs.
- `catalog/verification.json`: timestamped HTTP status, range, signature and length for every MP3.
- `catalog/build-report.json`: actual per-series counts, duplicates, exclusions and legacy migration information.
- `catalog/generated-files.json`: exact build output manifest.
- `dist/OshoWorld-Podcasts.zip`: validated feed/OPML/playlist package (generated, not committed).

All generated file and folder names are ASCII. Devanagari remains in UTF-8 RSS
text; filenames use source romanizations or existing ASCII names. OPML labels
use those ASCII names. ZIP members are checked for ASCII names; the Python ZIP
writer also sets the UTF-8 filename flag if non-ASCII names are ever permitted.
Legacy non-ASCII filenames are removed as part of the migration.

Validation checks every XML/OPML file, unique GUIDs, verified enclosure lengths,
strict date order, playlist parity, no stale feeds, and every OPML target. Tests
cover double-encoding, Hindi XML, invalid audio, numeric volume ordering, the
repaired Yoga track and the complete Tao series. ZIP integrity is tested too.

## Publish

GitHub Pages was already configured for `main` / root when inspected. With an
account that can write to `nirmalveer/osho`, merge `migrate-oshoworld` into `main`
and push. `.nojekyll` allows these static files to be served directly. No site
build service or audio storage is needed. After deployment, check the live Tao
feed contains 127 items before importing the OPML.

## Source references

- [OshoWorld Hindi catalog](https://oshoworld.com/api/audio/catalog/hindi)
- [OshoWorld English catalog](https://oshoworld.com/api/audio/catalog/english)
- [Tao's full track list](https://oshoworld.com/api/audio/get-all-audios-list/663b299a642f72069a88a64e)
- [Sannyas scraper reference](https://github.com/rishabh3354/sannyas/tree/master/tools): inspected for API and URL-encoding clues; no app code was copied or forked. The live site's full-list endpoint replaces its historical slug-guessing approach.
- [GitHub Pages publishing configuration](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)
