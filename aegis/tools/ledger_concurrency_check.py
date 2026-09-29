"""Live proof that the hash-chained ledger stays intact under real concurrent writers.

Fires N concurrent workflow starts for the same tenant against a running API (not mocked, not
sequential) and checks the resulting ledger has contiguous sequences, no gaps, no duplicates,
and reports `intact: true` from /v1/ledger/verify.

Usage: docker compose up -d, then
    python3 tools/ledger_concurrency_check.py
"""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

BASE_URL = "http://localhost:8000"
CONCURRENCY = 100


def start_run(client: httpx.Client, index: int) -> int:
    payload = {
        "workflow": "talent_acquisition",
        "subject_id": f"candidate-{index}",
        "context": {
            "recipient_email": f"c{index}@example.com",
            "subject": "hi",
            "body": "hello",
            "attendees": [f"c{index}@example.com"],
            "starts_at": "2026-10-01T10:00:00Z",
        },
    }
    resp = client.post(f"{BASE_URL}/v1/runs", json=payload)
    return resp.status_code


def main() -> int:
    global API_KEY
    if len(sys.argv) != 2:
        print("usage: ledger_concurrency_check.py <api_key>")
        return 2
    API_KEY = sys.argv[1]

    with httpx.Client(headers={"X-Api-Key": API_KEY}, timeout=30.0) as client:
        statuses: list[int] = []
        with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
            futures = [pool.submit(start_run, client, i) for i in range(CONCURRENCY)]
            for future in as_completed(futures):
                statuses.append(future.result())

        ok = sum(1 for s in statuses if s == 201)
        print(f"{ok}/{CONCURRENCY} runs accepted (201), statuses seen: {sorted(set(statuses))}")

        entries: list[dict] = []
        after: int | None = None
        while True:
            params = {"limit": 1000} if after is None else {"after": after, "limit": 1000}
            resp = client.get(f"{BASE_URL}/v1/ledger", params=params)
            resp.raise_for_status()
            page = resp.json()
            if not page:
                break
            entries.extend(page)
            after = page[-1]["sequence"]
            if len(page) < 1000:
                break

        sequences = sorted(e["sequence"] for e in entries)
        expected = list(range(sequences[0], sequences[0] + len(sequences))) if sequences else []
        contiguous = sequences == expected
        duplicates = len(sequences) != len(set(sequences))

        verify = client.get(f"{BASE_URL}/v1/ledger/verify").json()

        print(f"ledger entries: {len(entries)}, contiguous: {contiguous}, duplicates: {duplicates}")
        print(f"verify: {verify}")

        if not contiguous or duplicates or not verify["intact"]:
            print("FAIL: ledger integrity violated under concurrency")
            return 1

        print("PASS: ledger stayed contiguous, gap-free, hash-chain-intact under concurrency")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
