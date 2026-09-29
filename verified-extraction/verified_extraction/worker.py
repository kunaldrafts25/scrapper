"""Killable child process for a bounded one-shot crawl and extraction."""
from __future__ import annotations

import argparse
import base64
import importlib
import json
import subprocess
import sys
import time
from collections.abc import Callable

from .fetch import Page
from .models import JobRequest, Result
from .security import FetchError


def _page_to_wire(page: Page) -> dict:
    return {"url": page.url, "html": page.html, "fetched_at": page.fetched_at,
            "redirects": page.redirects, "raw": base64.b64encode(page.raw).decode("ascii"),
            "encoding": page.encoding, "decoding_errors": page.decoding_errors, "content_type": page.content_type}


def _page_from_wire(data: dict) -> Page:
    return Page(data["url"], data["html"], data["fetched_at"], data["redirects"],
                base64.b64decode(data["raw"], validate=True), data["encoding"], data["decoding_errors"],
                data.get("content_type", "text/html; charset=utf-8"))


def _run_child(callable_name: str) -> int:
    request = JobRequest.model_validate(json.load(sys.stdin))
    module_name, function_name = callable_name.split(":", 1)
    work = getattr(importlib.import_module(module_name), function_name)
    try:
        result, captures = work(request)
        message = {"kind": "ok", "result": result.model_dump(),
                   "captures": {digest: _page_to_wire(page) for digest, page in captures.items()}}
    except FetchError as exc:
        message = {"kind": "fetch_error", "code": exc.code, "message": str(exc)}
    except Exception:
        message = {"kind": "error", "code": "WORKER_FAILED", "message": "Worker failed without returning a result"}
    sys.stdout.write(json.dumps(message, ensure_ascii=True))
    return 0


def run_hard(request: JobRequest, on_tick: Callable[[], bool] | None = None,
             work: str = "verified_extraction.service:run_job"):
    """Enforce a wall-clock cap by killing the child on expiry or lost ownership."""
    started = time.monotonic()
    process = subprocess.Popen([sys.executable, "-m", "verified_extraction.worker", "--callable", work],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    payload = json.dumps(request.model_dump(by_alias=True), ensure_ascii=True).encode("utf-8")
    # communicate drains both pipes; timeout retries preserve buffered output.
    input_data = payload
    try:
        while True:
            remaining = started + request.options.deadline_seconds - time.monotonic()
            if remaining <= 0:
                raise FetchError("DEADLINE", "Whole-job wall-clock deadline reached")
            try:
                stdout, _ = process.communicate(input=input_data, timeout=min(0.25, remaining))
                break
            except subprocess.TimeoutExpired:
                input_data = None
                if on_tick is not None and not on_tick():
                    raise FetchError("CLAIM_LOST", "Job ownership was lost")
        if process.returncode != 0 or not stdout:
            raise FetchError("WORKER_FAILED", "Worker exited before returning a result")
        try:
            message = json.loads(stdout.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise FetchError("WORKER_FAILED", "Worker returned an invalid result") from exc
        if message["kind"] != "ok":
            raise FetchError(message["code"], message["message"])
        return Result.model_validate(message["result"]), {
            digest: _page_from_wire(page) for digest, page in message["captures"].items()}
    finally:
        if process.poll() is None:
            process.kill()
        try:
            process.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--callable", default="verified_extraction.service:run_job")
    args = parser.parse_args()
    raise SystemExit(_run_child(args.callable))


if __name__ == "__main__":
    main()
