"""Offline child used by the cross-process HTTPFetcher timing regression."""
import json
import os
import sys
import time

from verified_extraction import fetch


def main():
    database, output, delay, hold = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
    os.environ["VE_DB"] = database
    fetch.resolve_public = lambda host, port: "203.0.113.1"
    observed = {}

    class Response:
        status = 200
        def getheaders(self):
            return [("Content-Type", "text/html")]
        def read(self, size):
            time.sleep(hold)
            observed["finish"] = time.time()
            return b"<p>Support: Email</p>"

    class Connection:
        def __init__(self, *args):
            pass
        def connect(self):
            pass
        def request(self, method, path, headers):
            observed["start"] = time.time()
        def getresponse(self):
            return Response()
        def close(self):
            pass

    class Robots:
        def crawl_delay(self, user_agent):
            return delay

    fetch.PinnedHTTPS = Connection
    agent = fetch.HTTPFetcher({"example.com"}, time.monotonic() + 8)
    agent.robots["https://example.com"] = Robots()
    agent._request("https://example.com/page")
    with open(output, "w", encoding="utf-8") as file:
        json.dump(observed, file)


if __name__ == "__main__":
    main()
