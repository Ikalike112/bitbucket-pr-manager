#!/usr/bin/env python3
"""Approve Bitbucket Cloud pull requests from links; merge only with --merge.

Examples:
  python app/bitbucket_pr_batch.py https://bitbucket.org/example-team/service-api/pull-requests/12
  python app/bitbucket_pr_batch.py --file pr-links.txt --dry-run --merge
  python app/bitbucket_pr_batch.py --file pr-links.txt --target develop --merge

Requires BITBUCKET_API_TOKEN in the environment. Process links in input order;
put dependent PRs in separate runs. No third-party Python packages are needed.
"""

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen


PR_PATH = re.compile(
    r"^/([^/]+)/([^/]+)/(?:pull-requests|pullrequests)/(\d+)"
    r"(?:/(?:diff|commits))?/?$"
)


@dataclass(frozen=True)
class PullRequestRef:
    workspace: str
    repository: str
    number: int

    @property
    def url(self):
        return (
            f"https://bitbucket.org/{self.workspace}/{self.repository}"
            f"/pull-requests/{self.number}"
        )

    @property
    def api_path(self):
        workspace = quote(self.workspace, safe="")
        repository = quote(self.repository, safe="")
        return f"/repositories/{workspace}/{repository}/pullrequests/{self.number}"


def parse_pr_url(value):
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or parsed.netloc.lower() != "bitbucket.org":
        raise ValueError(f"Expected an https://bitbucket.org PR link: {value}")
    match = PR_PATH.fullmatch(parsed.path)
    if not match:
        raise ValueError(f"Expected /workspace/repository/pull-requests/number: {value}")
    return PullRequestRef(match.group(1), match.group(2), int(match.group(3)))


def collect_refs(urls, filename):
    lines = list(urls)
    if filename:
        if filename == "-":
            lines.extend(sys.stdin.read().splitlines())
        else:
            lines.extend(Path(filename).read_text(encoding="utf-8-sig").splitlines())
    refs = []
    seen = set()
    for line in lines:
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        ref = parse_pr_url(value)
        if ref not in seen:
            refs.append(ref)
            seen.add(ref)
    return refs


class BitbucketError(Exception):
    pass


class BitbucketAPI:
    def __init__(self, token):
        self.token = token

    def call(self, method, path, payload=None):
        data = None
        if method == "POST":
            data = json.dumps(payload).encode("utf-8") if payload is not None else b""
        request = Request(
            "https://api.bitbucket.org/2.0" + path,
            data=data,
            method=method,
            headers={
                "Authorization": "Bearer " + self.token,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=30) as response:
                body = response.read()
                try:
                    return json.loads(body) if body else {}
                except json.JSONDecodeError as error:
                    raise BitbucketError("Bitbucket returned invalid JSON") from error
        except HTTPError as error:
            detail = error.read(1200).decode("utf-8", errors="replace")
            raise BitbucketError(f"HTTP {error.code}: {detail}") from error
        except (URLError, TimeoutError) as error:
            raise BitbucketError(str(getattr(error, "reason", error))) from error


def approved_by(pr, user_uuid):
    return any(
        participant.get("user", {}).get("uuid") == user_uuid
        and participant.get("approved") is True
        for participant in pr.get("participants", [])
    )


def process_pr(api, ref, user_uuid, *, approve=True, merge=False, dry_run=False, target=None):
    if not approve and not merge:
        raise BitbucketError("select approve, merge, or both")
    pr = api.call("GET", ref.api_path)
    state = pr.get("state")
    destination = pr.get("destination", {}).get("branch", {}).get("name")
    source_hash = pr.get("source", {}).get("commit", {}).get("hash")
    if target and destination != target:
        raise BitbucketError(f"target is {destination!r}, expected {target!r}")
    if state == "MERGED":
        return "already merged"
    if state != "OPEN":
        raise BitbucketError(f"PR state is {state!r}, expected OPEN")

    already_approved = approved_by(pr, user_uuid)
    if dry_run:
        action = ("already approved" if already_approved else "approve") if approve else ""
        if merge:
            action += ", then merge" if action else "merge"
        return f"PLAN {action} into {destination} ({source_hash[:12] if source_hash else 'unknown SHA'})"

    if approve and not already_approved:
        api.call("POST", ref.api_path + "/approve")
        pr = api.call("GET", ref.api_path)
        if not approved_by(pr, user_uuid):
            raise BitbucketError("approval was not confirmed on the PR")

    if not merge:
        return "already approved" if already_approved else "approved"

    pr = api.call("GET", ref.api_path)
    if pr.get("state") != "OPEN":
        raise BitbucketError(f"PR state changed to {pr.get('state')!r} before merge")
    if pr.get("destination", {}).get("branch", {}).get("name") != destination:
        raise BitbucketError("destination branch changed during processing; merge skipped")
    current_hash = pr.get("source", {}).get("commit", {}).get("hash")
    if current_hash != source_hash:
        raise BitbucketError("source commit changed during approval; merge skipped")
    result = api.call(
        "POST",
        ref.api_path + "/merge",
        {"merge_strategy": "merge_commit", "close_source_branch": False},
    )
    if result.get("state") != "MERGED":
        result = api.call("GET", ref.api_path)
    if result.get("state") != "MERGED":
        raise BitbucketError(f"merge was not confirmed; current state: {result.get('state')!r}")
    return "merged"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Approve selected Bitbucket Cloud PRs; merge only with --merge.",
        epilog=(
            "Examples:\n"
            "  python app/bitbucket_pr_batch.py https://bitbucket.org/example-team/service-api/pull-requests/12\n"
            "  python app/bitbucket_pr_batch.py --file pr-links.txt --dry-run --merge\n"
            "  python app/bitbucket_pr_batch.py --file pr-links.txt --target develop --merge"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("urls", nargs="*", help="Bitbucket Cloud PR links")
    parser.add_argument("--file", help="UTF-8 text file with one PR link per line; '-' reads stdin")
    parser.add_argument("--merge", action="store_true", help="merge each PR after approving it")
    parser.add_argument("--dry-run", action="store_true", help="show actions without changing PRs")
    parser.add_argument("--target", help="require this destination branch, e.g. develop")
    args = parser.parse_args(argv)

    try:
        refs = collect_refs(args.urls, args.file)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    if not refs:
        parser.error("provide PR links or --file")
    token = os.environ.get("BITBUCKET_API_TOKEN")
    if not token:
        parser.error("set BITBUCKET_API_TOKEN in the environment")

    api = BitbucketAPI(token)
    try:
        me = api.call("GET", "/user")
        user_uuid = me["uuid"]
    except (BitbucketError, KeyError) as error:
        print(f"Cannot identify Bitbucket user: {error}", file=sys.stderr)
        return 1

    failed = 0
    for ref in refs:
        try:
            outcome = process_pr(
                api, ref, user_uuid,
                merge=args.merge, dry_run=args.dry_run, target=args.target,
            )
            print(f"OK   {ref.url} -> {outcome}")
        except BitbucketError as error:
            failed += 1
            print(f"FAIL {ref.url} -> {error}", file=sys.stderr)
    print(f"Processed {len(refs)} PR(s); failed: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
