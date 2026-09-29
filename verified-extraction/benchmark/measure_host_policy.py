"""Measure the host lease with two offline HTTPFetcher worker processes."""
import json
import subprocess
import sys
import uuid
from pathlib import Path


def main():
    stem = f"measure-host-{uuid.uuid4().hex}"
    database = Path(stem + ".db")
    outputs = [Path(stem + f"-{index}.json") for index in range(2)]
    processes = []
    try:
        for output in outputs:
            processes.append(subprocess.Popen([sys.executable, "-m", "tests.offline_host_worker",
                str(database), str(output), "0.8", "0.35"]))
        for process in processes:
            if process.wait(timeout=10) != 0:
                raise RuntimeError("Offline HTTP worker failed")
        observations = sorted((json.loads(output.read_text(encoding="utf-8")) for output in outputs),
                              key=lambda row: row["start"])
        gap = observations[1]["start"] - observations[0]["start"]
        overlap = max(0, observations[0]["finish"] - observations[1]["start"])
        print(json.dumps({"network_calls": 0, "request_start_finish_utc_epoch": observations,
            "start_gap_seconds": round(gap, 4), "overlap_seconds": round(overlap, 4),
            "required_start_gap_seconds": 0.8}, indent=2))
        if gap < 0.77 or overlap > 0.005:
            raise SystemExit("Host policy timing failed")
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
        for output in outputs:
            output.unlink(missing_ok=True)
        database.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
