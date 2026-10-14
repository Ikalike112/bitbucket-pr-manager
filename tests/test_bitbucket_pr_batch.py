import copy
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from bitbucket_pr_batch import (
    BitbucketAPI,
    BitbucketError,
    PullRequestRef,
    collect_refs,
    main,
    parse_pr_url,
    process_pr,
)


class FakeAPI:
    def __init__(self, *, source_change=False, destination="develop", destination_change=False):
        self.pr = {
            "state": "OPEN",
            "destination": {"branch": {"name": destination}},
            "source": {"commit": {"hash": "abc123"}},
            "participants": [],
        }
        self.calls = []
        self.source_change = source_change
        self.destination_change = destination_change

    def call(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if path.endswith("/approve"):
            self.pr["participants"] = [{"user": {"uuid": "user-1"}, "approved": True}]
            if self.source_change:
                self.pr["source"]["commit"]["hash"] = "changed"
            if self.destination_change:
                self.pr["destination"]["branch"]["name"] = "changed-target"
        elif path.endswith("/merge"):
            self.pr["state"] = "MERGED"
        return copy.deepcopy(self.pr)


class PullRequestBatchTests(unittest.TestCase):
    def setUp(self):
        self.ref = PullRequestRef("example-team", "service-api", 2)

    def test_parse_pr_link_and_reject_foreign_host(self):
        self.assertEqual(parse_pr_url(self.ref.url + "/diff#change"), self.ref)
        with self.assertRaises(ValueError):
            parse_pr_url("https://bitbucket.org.evil.test/example-team/repo/pull-requests/2")

    def test_collect_file_deduplicates_and_preserves_order(self):
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / "links.txt"
            filename.write_text("# batch\n" + self.ref.url + "\n" + self.ref.url + "\n")
            self.assertEqual(collect_refs([], str(filename)), [self.ref])

    def test_approve_does_not_merge_by_default(self):
        api = FakeAPI()
        self.assertEqual(process_pr(api, self.ref, "user-1"), "approved")
        self.assertEqual([call[1].split("/")[-1] for call in api.calls], ["2", "approve", "2"])

    def test_empty_approval_post_sends_valid_json(self):
        with patch("bitbucket_pr_batch.urlopen", return_value=io.BytesIO(b"{}")) as send:
            BitbucketAPI("test-token").call("POST", "/repositories/example/repo/pullrequests/2/approve")
        request = send.call_args.args[0]
        self.assertEqual(request.data, b"{}")
        self.assertEqual(request.get_header("Content-type"), "application/json")

    def test_already_approved_does_not_post_again(self):
        api = FakeAPI()
        api.pr["participants"] = [{"user": {"uuid": "user-1"}, "approved": True}]
        self.assertEqual(process_pr(api, self.ref, "user-1"), "already approved")
        self.assertEqual([call[0] for call in api.calls], ["GET"])

    def test_merge_requires_explicit_option(self):
        api = FakeAPI()
        self.assertEqual(process_pr(api, self.ref, "user-1", merge=True), "merged")
        self.assertEqual(api.calls[-1][1].split("/")[-1], "merge")

    def test_merge_only_does_not_approve(self):
        api = FakeAPI()
        self.assertEqual(process_pr(api, self.ref, "user-1", approve=False, merge=True), "merged")
        self.assertFalse(any(call[1].endswith("/approve") for call in api.calls))

    def test_dry_run_never_posts(self):
        api = FakeAPI()
        self.assertIn("PLAN approve, then merge", process_pr(api, self.ref, "user-1", merge=True, dry_run=True))
        self.assertEqual(len(api.calls), 1)

    def test_preview_reports_existing_approval_without_changing_pr(self):
        api = FakeAPI()
        api.pr["participants"] = [{"user": {"uuid": "user-1"}, "approved": True}]
        plan, already_approved = process_pr(
            api, self.ref, "user-1", merge=True, dry_run=True,
            include_approval_state=True,
        )
        self.assertTrue(already_approved)
        self.assertIn("already approved, then merge", plan)
        self.assertEqual([call[0] for call in api.calls], ["GET"])

    def test_preview_of_merged_pr_does_not_report_approval(self):
        api = FakeAPI()
        api.pr["state"] = "MERGED"
        self.assertEqual(
            process_pr(api, self.ref, "user-1", merge=True, dry_run=True,
                       include_approval_state=True),
            ("already merged", False),
        )

    def test_approve_and_merge_use_destination_from_each_pr(self):
        for destination in (
            "develop",
            "feature/prebrain-guardrails-20261008",
            "feature/admin-brd-hierarchy-20261012",
        ):
            with self.subTest(destination=destination):
                api = FakeAPI(destination=destination)
                self.assertEqual(process_pr(api, self.ref, "user-1", merge=True), "merged")
                self.assertEqual(api.pr["destination"]["branch"]["name"], destination)
                self.assertEqual(
                    [call[1].split("/")[-1] for call in api.calls if call[0] == "POST"],
                    ["approve", "merge"],
                )

    def test_cli_preview_accepts_mixed_destinations_without_posting(self):
        destinations = {
            PullRequestRef("animaplay", "ree.api.admin", 6):
                "feature/prebrain-guardrails-20261008",
            PullRequestRef("animaplay", "ree.service.usermanagement", 5):
                "feature/admin-brd-hierarchy-20261012",
        }
        prs = {
            ref.api_path: FakeAPI(destination=destination).pr
            for ref, destination in destinations.items()
        }
        output = io.StringIO()
        with patch.dict("os.environ", {"BITBUCKET_API_TOKEN": "test-token"}), \
                patch("bitbucket_pr_batch.BitbucketAPI") as api_class, \
                patch("sys.stdout", output):
            api_class.return_value.call.side_effect = lambda method, path: (
                {"uuid": "user-1"} if path == "/user" else copy.deepcopy(prs[path])
            )
            result = main(["--dry-run", "--merge", *(ref.url for ref in destinations)])
        self.assertEqual(result, 0)
        for destination in destinations.values():
            self.assertIn("into " + destination, output.getvalue())
        self.assertIn("Processed 2 PR(s); failed: 0", output.getvalue())
        self.assertTrue(all(
            call.args[0] == "GET" for call in api_class.return_value.call.call_args_list
        ))

    def test_destination_change_prevents_merge(self):
        api = FakeAPI(destination_change=True)
        with self.assertRaisesRegex(BitbucketError, "destination branch changed"):
            process_pr(api, self.ref, "user-1", merge=True)
        self.assertFalse(any(call[1].endswith("/merge") for call in api.calls))

    def test_source_change_prevents_merge(self):
        api = FakeAPI(source_change=True)
        with self.assertRaises(BitbucketError):
            process_pr(api, self.ref, "user-1", merge=True)
        self.assertFalse(any(call[1].endswith("/merge") for call in api.calls))


if __name__ == "__main__":
    unittest.main()
