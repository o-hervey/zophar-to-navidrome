# Changelog

## 0.9.2
- Minimum-length filter (`MIN_TRACK_SECONDS`, default 20 s) now also applies to Zophar's MP3 downloads.

## 0.9.1
- numpy pinned to 1.26 so the image runs on VMs with a basic CPU type; a failed numpy import now fails the job loudly
  instead of silently skipping the loop check.

## 0.9
- Looping sound effects (the NES noise channel never repeats sample for sample) are caught by comparing loudness
  patterns across frequency bands; `MAX_LOOP_SECONDS` (default 10 s) controls it.

## 0.8
- Tracks with no title are named `Track N`.

## 0.6 - 0.7
- Sound effects and jingles shorter than `MIN_TRACK_SECONDS` are skipped; tracks that play once are trimmed to where the
  sound stops.

## 0.5
- New look: light and dark themes, per-console colour chips, stepped progress meter in the queue.

## 0.4
- Results as an aligned table; Download turns into the job status in place; sticky queue panel.

## 0.3 and earlier
- Search Zophar's Domain, queue downloads, render NSF/SPC/GBS/... with ffmpeg + libgme or retag Zophar's MP3s, cover
  art, atomic album publishing, Navidrome rescan.
