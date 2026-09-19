# Contributing

Read AGENTS.md and the current milestone/status before changing code. Work on a focused branch and open a pull request; do not push implementation changes directly to protected main. Preserve original media, browser state and the video-only/soft-subtitle invariants.

## Validation and merge gate

Require hosted checks named **macOS build**, **Python tests**, and **Review evidence** on the current PR artifact, plus resolved independent review findings. CI compiles the macOS app, verifies its ad-hoc signature, and runs Python tests with small synthetic fixtures. FFmpeg is installed only on the disposable Linux CI runner. It does not test an Apple TV, external websites, browser extensions, distribution signing or a production release. No extension exists yet, so there is no browser-test claim.

Independent review may be performed by another human or a separately assigned agent that did not implement the covered change. Record the actual reviewer identifier and its returned findings. Agent review evidence is not a GitHub approval, and a repository owner's impossible self-approval is not required. Repository protection and the merge operator enforce the review policy; the hash checker establishes only artifact consistency and nonempty metadata. Do not manufacture reviewer identities or findings to satisfy it.

Merge only within explicit current user authorization after those gates pass. Keep a PR open when required checks or material findings remain unresolved. A merge does not authorize releasing binaries, notarization, deployment or publication elsewhere.

## Exact-artifact review evidence

Stage every intended source, script, workflow and document first. Generate a draft outside the evidence record:

```sh
python3 scripts/check_review_evidence.py --snapshot > work/review-draft.json
```

The snapshot covers every Git-tracked regular file except generated `work/`, `artifacts/`, and JSON review records under `reviews/`. This avoids recursively hashing the evidence that contains the hashes. No source, test, workflow or governing document is exempt. Newly created files must be staged before producing the snapshot.

Send the immutable snapshot and bounded file assignments to independent reviewers. After they return, save `reviews/current.json` using schema version 1:

```json
{
  "schema_version": 1,
  "source_sha256": {"relative/path": "64-character SHA-256 from snapshot"},
  "reviews": [
    {
      "reviewer_id": "actual reviewer or independent agent identifier",
      "reviewed_at_utc": "2026-09-19T00:00:00Z",
      "covered_paths": ["relative/path"],
      "summary": "What was inspected and the observed result",
      "findings": [],
      "disposition": "approved"
    }
  ]
}
```

Use the generated complete hash map, not the illustrative placeholder above. The union of reviewers' covered paths must include every snapshot file. Keep material findings and their resolution in the record; `approved` means the reviewer reported no unresolved blocking findings. Do not reinterpret a review requesting changes as approval.

```sh
python3 scripts/check_review_evidence.py reviews/current.json
```

After any covered file changes, regenerate its snapshot and obtain review of the changed artifact before updating the record. Existing reviews can remain only for byte-identical covered files. Verify coverage after final governance/document edits as well. The checker cannot authenticate identities, establish independence or prove that reviewers actually inspected files; retain their real review messages as supporting evidence and report this boundary honestly.

Before publication, remove private machine paths, user media, active session URLs/tokens, cookies and browsing history from tracked files and PR text. Describe physical testing separately, including device/software versions and what was directly observed.
