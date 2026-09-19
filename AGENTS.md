# VideoBridge working agreements

Read README.md and docs/STATUS.md before implementation. Read only the plan sections relevant to the current change. User instructions override this file.

## Product invariants

- Video-only playback: never turn on display mirroring or screen capture as an implicit fallback.
- Soft subtitles are first-class, selectable receiver media. A local overlay is not proof of TV subtitles. Never silently omit a requested subtitle track.
- Preserve the user's browser session and original files. Do not rewrite media, pause unrelated players, scrape unrelated tabs, transfer cookies indiscriminately, or change default file associations.
- Prefer direct play, then remux, then hardware transcode. Report quality, styling, HDR or audio losses before applying a lossy route.
- A browser/site/format is supported only after its acceptance case passes on Apple TV. A compiled prototype or local decode is not that evidence.

## Development

- Work milestone by milestone, starting with M0 in docs/ROADMAP.md. No broad feature expansion before the transport/subtitle proof.
- Keep source discovery, media planning, subtitle conversion, local serving, process management and receiver playback behind separate contracts. The early prototype is not the final architecture.
- Site adapters return the shared media descriptor. No website-specific logic in playback, relay or subtitle modules.
- Use versioned messages and explicit capability/error responses; reject unknown major protocol versions.
- Add small regression fixtures for actual failure classes. Keep deterministic tests separate from physical receiver tests and changing external-site checks.
- Keep docs/STATUS.md current: requirement, result, exact evidence, unresolved blocker, next action. Append meaningful decisions; do not erase history.
- Do not add agents, remote services, dependencies or abstractions merely for anticipated expansion. Delegate only when the user or applicable higher-priority instructions authorize it.

## Storage and privacy

Before builds, media generation, installs or artifact-heavy testing, check df -Pk / and estimate peak space. Stop before 15,728,640 KiB free; never proceed at/below 14,680,064 KiB without user instruction. Session caches must be bounded and owned, with cancellation and cleanup tests. Never delete originals or pre-existing user files to reclaim space.

Keep disposable build/test files in work/, generated app archives in artifacts/, and synthetic fixtures generated from scripts. Do not commit URLs with credentials/tokens, cookies, browsing history, personal media, build caches or binaries. Diagnostic events must redact query strings, credentials and private paths. No changes to live Codex history/databases.
