"""Export protected HumanProof sessions with a checksum and provenance counts."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import quote
import uuid

if __package__:
    from .demo_bot import ApiClient, ApiError, FIXTURE_SOURCE, REPO_DIR, analyst_key, base_url
else:
    from demo_bot import ApiClient, ApiError, FIXTURE_SOURCE, REPO_DIR, analyst_key, base_url


def output_directory(value):
    path = Path(value)
    if not path.is_absolute():
        path = REPO_DIR / path
    resolved = path.resolve()
    allowed = (REPO_DIR / "data" / "exports").resolve()
    if not allowed.is_relative_to(REPO_DIR) or not resolved.is_relative_to(allowed):
        raise argparse.ArgumentTypeError("Export directories must be inside this project's data/exports directory.")
    return resolved


def export_sessions(client, key, destination):
    started = datetime.now(timezone.utc)
    run_name = started.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    run_dir = destination / run_name
    run_dir.mkdir(parents=True, exist_ok=False)
    partial_path = run_dir / "sessions.jsonl.partial"
    headers = {"X-Analyst-Key": key}
    seen, labels, sources = set(), Counter(), Counter()
    total_events = synthetic = truncated = 0
    checksum = hashlib.sha256()
    page = 1
    with partial_path.open("xb") as output:
        while True:
            listing = client.request(f"/api/v1/sessions/?page={page}&page_size=100", headers=headers, retries=2)
            records = listing["results"]
            if not records:
                break
            for record in records:
                session_id = record["id"]
                if session_id in seen:
                    continue
                seen.add(session_id)
                data = client.request(f"/api/v1/sessions/{quote(session_id, safe='')}/export/",
                                      headers=headers, retries=2)
                line = (json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
                output.write(line)
                checksum.update(line)
                labels[data.get("label", "unknown")] += 1
                sources[data.get("label_source") or "unlabeled"] += 1
                total_events += len(data.get("events", []))
                synthetic += data.get("label_source") == FIXTURE_SOURCE
                truncated += bool(data.get("truncated"))
            if page * 100 >= listing["count"]:
                break
            page += 1
    data_path = run_dir / "sessions.jsonl"
    partial_path.rename(data_path)
    manifest = {
        "export_schema": "humanproof-dataset-v1",
        "started_at": started.isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "data_file": data_path.name,
        "sha256": checksum.hexdigest(),
        "session_count": len(seen),
        "event_count": total_events,
        "label_counts": {name: labels[name] for name in ("human", "bot", "unknown")},
        "label_source_counts": dict(sorted(sources.items())),
        "synthetic_labeled_session_count": synthetic,
        "truncated_session_count": truncated,
        "limitations": [
            "Live API export, not a transactionally frozen dataset. Pause collection for a stable snapshot.",
            "Labels preserve analyst provenance and may need review before training.",
            "Synthetic API fixtures test the pipeline and are not evidence of real browser detection.",
            "Keep related participants and automation runs together when creating evaluation splits.",
        ],
    }
    manifest_path = run_dir / "manifest.json"
    with manifest_path.open("x", encoding="ascii", newline="\n") as output:
        json.dump(manifest, output, ensure_ascii=True, indent=2, sort_keys=True)
        output.write("\n")
    print(f"Exported {len(seen)} sessions and {total_events} events to {data_path}")
    print(f"Labels: human={labels['human']}, bot={labels['bot']}, unknown={labels['unknown']}")
    print(f"Manifest: {manifest_path}")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Export paginated HumanProof sessions to JSONL with a SHA256 manifest.",
        epilog="Uses HUMANPROOF_ANALYST_KEY or backend/.analyst-key. Each run creates a new directory. "
               "Exports contain session telemetry and stay in the ignored data/exports directory. "
               "A .partial file without a manifest indicates an incomplete run.",
    )
    parser.add_argument("--base-url", type=base_url, default="http://127.0.0.1:8000",
                        help="Backend address (default: http://127.0.0.1:8000).")
    parser.add_argument("--output-dir", type=output_directory, default="data/exports",
                        help="Parent directory inside data/exports (default: data/exports).")
    args = parser.parse_args(argv)
    try:
        key = analyst_key()
        if not key:
            raise ApiError("Analyst access is required. Run init_demo or set HUMANPROOF_ANALYST_KEY.")
        export_sessions(ApiClient(args.base_url), key, args.output_dir)
    except (ApiError, OSError, UnicodeError, KeyError, TypeError) as error:
        message = str(error) if isinstance(error, ApiError) else "Export failed while reading or writing session data."
        print(message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
