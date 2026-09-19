#!/usr/bin/env python3
"""Check exact tracked-file coverage; this cannot authenticate reviewer independence."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def snapshot():
    paths = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    result = {}
    for name in sorted(filter(None, paths)):
        path = Path(name)
        if path.parts[0] in {'work', 'artifacts'} or (path.parts[0] == 'reviews' and path.suffix == '.json'):
            continue
        target = ROOT / path
        if target.is_symlink() or not target.is_file():
            raise ValueError(f'Reviewed entry must be a regular file: {name}')
        result[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    if not result:
        raise ValueError('No tracked source files found. Stage the intended files first.')
    return result


def validate(record, expected):
    if record.get('schema_version') != 1:
        raise ValueError('Unsupported review evidence schema.')
    if record.get('source_sha256') != expected:
        actual = record.get('source_sha256', {})
        changed = sorted(k for k in expected.keys() | actual.keys() if actual.get(k) != expected.get(k))
        raise ValueError('Review evidence does not match tracked files: ' + ', '.join(changed))
    reviewers = record.get('reviews')
    if not isinstance(reviewers, list) or not reviewers:
        raise ValueError('At least one recorded independent review is required.')
    covered = set()
    for review in reviewers:
        for key in ('reviewer_id', 'reviewed_at_utc', 'summary'):
            if not isinstance(review.get(key), str) or not review[key].strip():
                raise ValueError(f'Reviewer metadata missing: {key}')
        if review.get('disposition') != 'approved':
            raise ValueError('Every included review must have resolved findings and approved disposition.')
        paths = review.get('covered_paths')
        if not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p in expected for p in paths):
            raise ValueError('Reviewer coverage must name existing snapshot paths.')
        if not isinstance(review.get('findings'), list):
            raise ValueError('Include findings as a list; use [] when no actionable findings remain.')
        covered.update(paths)
    if covered != set(expected):
        raise ValueError('Unreviewed paths: ' + ', '.join(sorted(set(expected) - covered)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', action='store_true', help='Print unsigned review skeleton after staging all intended files.')
    parser.add_argument('record', nargs='?', default='reviews/current.json')
    args = parser.parse_args()
    try:
        expected = snapshot()
        if args.snapshot:
            print(json.dumps(dict(schema_version=1, source_sha256=expected, reviews=[]), indent=2))
            return
        record = json.loads((ROOT / args.record).read_text())
        validate(record, expected)
        print(f'PASS: {len(expected)} tracked files match recorded review hashes and coverage.')
        print('This verifies artifact consistency and metadata, not reviewer identity or independence.')
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f'Review evidence failed: {exc}')


if __name__ == '__main__':
    main()
