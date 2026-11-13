#!/usr/bin/env python3
"""
Structural locks on the GitHub workflows (parsed as text; no YAML dependency).

These pin the security shape that is easy to erode in a later edit:
  - every action is pinned to a full commit SHA;
  - the anchor workflow keeps third-party code (the PyPI-installed OpenTimestamps
    client) and the token that can push to main in SEPARATE jobs, with the hand-over
    going through scripts/accept_anchors.py;
  - that client is installed from a hash-locked requirements file;
  - long-running jobs have timeouts, and the heartbeat steps stay optional no-ops.
Stdlib only.
"""
import glob
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF = os.path.join(REPO_ROOT, ".github", "workflows")


def read(name):
    with open(os.path.join(WF, name)) as f:
        return f.read()


def job(text, name):
    """The text of one job: from its `  name:` line to the next 2-space key or EOF."""
    m = re.search(r"(?ms)^  %s:\n(.*?)(?=^  \S|\Z)" % re.escape(name), text)
    assert m, "job %r not found" % name
    return m.group(1)


def code(text):
    """Workflow text with YAML comments stripped (so prose can't satisfy or trip a check)."""
    return "\n".join(re.sub(r"(^|\s)#.*$", "", line) for line in text.splitlines())


class TestAllWorkflows(unittest.TestCase):
    def test_every_action_is_pinned_to_a_full_commit_sha(self):
        uses = []
        for path in glob.glob(os.path.join(WF, "*.yml")):
            with open(path) as f:
                uses += [(os.path.basename(path), u) for u in re.findall(r"uses:\s*(\S+)", f.read())]
        self.assertGreater(len(uses), 8)
        for wf, u in uses:
            self.assertRegex(u, r"^actions/[a-z-]+@[0-9a-f]{40}$", "%s: unpinned or third-party action %s" % (wf, u))

    def test_jobs_that_touch_the_network_have_timeouts(self):
        for wf, name in (("refresh-pages.yml", "refresh"), ("anchor-head.yml", "stamp"),
                         ("anchor-head.yml", "commit"), ("keepalive.yml", "keepalive")):
            self.assertRegex(job(read(wf), name), r"(?m)^    timeout-minutes:\s*\d+", "%s/%s" % (wf, name))


class TestAnchorWorkflowIsolation(unittest.TestCase):
    def setUp(self):
        self.text = read("anchor-head.yml")
        self.stamp = code(job(self.text, "stamp"))
        self.commit = code(job(self.text, "commit"))

    def test_default_token_is_read_only_and_only_commit_can_write(self):
        self.assertRegex(self.text, r"(?m)^permissions:\n  contents: read\n")
        self.assertRegex(self.stamp, r"permissions:\n\s+contents: read")
        self.assertNotIn("contents: write", self.stamp)
        self.assertRegex(self.commit, r"permissions:\n\s+contents: write")

    def test_stamp_job_never_sees_a_push_token(self):
        self.assertNotIn("GITHUB_TOKEN", self.stamp)
        self.assertNotIn("secrets.", self.stamp)
        self.assertNotIn("git push", self.stamp)
        self.assertRegex(self.stamp, r"persist-credentials:\s*false")

    def test_stamp_job_installs_only_the_hash_locked_set(self):
        installs = re.findall(r"pip install[^\n]*", self.stamp)
        self.assertEqual(len(installs), 1)
        self.assertIn("--require-hashes", installs[0])
        self.assertIn("--only-binary=:all:", installs[0])
        self.assertIn("-r ops/anchor-requirements.txt", installs[0])

    def test_commit_job_runs_no_third_party_code(self):
        for forbidden in ("pip install", "pip3 install", "ots stamp", "ots upgrade", "npm ", "curl -o", "| sh", "| bash"):
            self.assertNotIn(forbidden, self.commit, "commit job must not run: " + forbidden)
        self.assertRegex(self.commit, r"needs:\s*stamp")
        self.assertRegex(self.commit, r"persist-credentials:\s*false")

    def test_artifact_is_admitted_through_the_gate_before_any_commit(self):
        gate = self.commit.index("python scripts/accept_anchors.py \"$RUNNER_TEMP/incoming\"")
        self.assertLess(self.commit.index("download-artifact"), gate)
        self.assertLess(gate, self.commit.index("git add anchors"))
        self.assertLess(self.commit.index("git push"), self.commit.index("accept_anchors.py --check-pending"))
        # the artifact is extracted OUTSIDE the checkout, never straight into anchors/
        self.assertRegex(self.commit, r"path:\s*\$\{\{ runner\.temp \}\}/incoming")

    def test_push_is_retried_with_a_rebase(self):
        self.assertIn("for attempt in 1 2 3", self.commit)
        self.assertIn("rebase FETCH_HEAD", self.commit)
        self.assertNotRegex(self.commit, r"push[^\n]*(--force|-f\b)")


class TestAnchorRequirementsLock(unittest.TestCase):
    def test_every_requirement_is_an_exact_pin_with_a_sha256(self):
        with open(os.path.join(REPO_ROOT, "ops", "anchor-requirements.txt")) as f:
            body = "\n".join(l for l in f.read().splitlines() if l.strip() and not l.lstrip().startswith("#"))
        reqs = re.findall(r"(?m)^(\S+)==(\S+) \\\n    --hash=sha256:([0-9a-f]{64})$", body)
        names = [r[0] for r in reqs]
        self.assertIn("opentimestamps-client", names)
        self.assertEqual(len(names), len(set(names)))
        # nothing else in the file: no unpinned line, no extra index, no editable/VCS install
        self.assertEqual(len(body.splitlines()), 2 * len(reqs))
        self.assertGreaterEqual(len(reqs), 9)


class TestHeartbeats(unittest.TestCase):
    def test_heartbeat_steps_are_optional_and_never_fail_the_run(self):
        for wf, name, secret in (("refresh-pages.yml", "refresh", "HEARTBEAT_URL"),
                                 ("anchor-head.yml", "commit", "ANCHOR_HEARTBEAT_URL")):
            j = code(job(read(wf), name))
            step = j[j.index("Heartbeat"):]
            self.assertIn("secrets." + secret, step)
            self.assertRegex(step, r'if \[ -z "\$HEARTBEAT_URL" \]; then[^\n]*exit 0')      # unset secret = no-op
            self.assertRegex(step, r'curl -fsS -m \d+ [^\n]*"\$HEARTBEAT_URL" \|\| echo')     # a failed ping is not a failed run
            self.assertNotIn("${{ secrets.%s }}" % secret, step.split("run:")[1])            # never interpolated into the shell

    def test_heartbeats_only_fire_after_a_fully_healthy_run(self):
        for wf, name in (("refresh-pages.yml", "refresh"), ("anchor-head.yml", "commit")):
            j = code(job(read(wf), name))
            step = j[j.index("Heartbeat"):]
            # no `if: always()` and no error swallowing on the step itself ...
            self.assertNotRegex(step, r"(?m)^\s+(if|continue-on-error):")
        # ... and in the anchor commit job nothing from the gate onward may be swallowed
        commit = code(job(read("anchor-head.yml"), "commit"))
        self.assertNotIn("continue-on-error", commit[commit.index("accept_anchors.py"):])

    def test_refresh_heartbeat_comes_after_the_degraded_alarm(self):
        j = read("refresh-pages.yml")
        self.assertLess(j.index("Alarm on a degraded refresh"), j.index("Heartbeat (dead-man"))


if __name__ == "__main__":
    unittest.main()
