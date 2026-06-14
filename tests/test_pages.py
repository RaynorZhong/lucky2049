#!/usr/bin/env python3
"""
Structural guards for the static pages (parsed as text, no browser):
  - the persistent top nav is duplicated verbatim across all 5 pages, so this checks
    they don't drift (same link set/order) and each marks its own page aria-current;
  - the stats plain-language read keeps the right threshold + direction;
  - the homepage "Earlier draws" list de-dups the featured latest by id.
Stdlib only.
"""
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(REPO_ROOT, "web")
PAGES = ["index", "verify", "stats", "trend", "randomness"]


def read(page):
    with open(os.path.join(WEB, page + ".html")) as f:
        return f.read()


def nav_block(html):
    m = re.search(r'<div class="tn-links">(.*?)</div>', html, re.S)
    return m.group(1) if m else ""


def nav_links(html):
    return [(href, text.strip()) for href, text in
            re.findall(r'<a href="([^"]+)"[^>]*>([^<]+)</a>', nav_block(html))]


class TestNavConsistency(unittest.TestCase):
    def test_nav_identical_across_pages(self):
        ref = nav_links(read("index"))
        self.assertGreaterEqual(len(ref), 6)   # Home Verify Statistics Trend Randomness GitHub
        self.assertEqual([t for _, t in ref], ["Home", "Verify", "Statistics", "Trend", "Randomness", "GitHub ↗"])
        for p in PAGES:
            self.assertEqual(nav_links(read(p)), ref, "top nav drifted on " + p + ".html")

    def test_each_page_marks_its_own_active_link(self):
        for p in PAGES:
            cur = re.findall(r'<a href="([^"]+)"[^>]*aria-current="page"[^>]*>', nav_block(read(p)))
            self.assertEqual(len(cur), 1, "expected exactly one aria-current on " + p + ".html, got " + str(cur))
            self.assertEqual(cur[0], "./" + p + ".html", "wrong active link on " + p + ".html")


class TestPageStructure(unittest.TestCase):
    def test_stats_plain_read_threshold_and_direction(self):
        m = re.search(r'p > 0\.05\s*\?\s*"([^"]*)"\s*:\s*"([^"]*)"', read("stats"))
        self.assertIsNotNone(m, "stats plain-language read ternary not found")
        self.assertTrue(m.group(1).startswith("Looks fair"), "p>0.05 branch should read 'Looks fair'")
        self.assertTrue(m.group(2).startswith("Worth a closer look"), "p<=0.05 branch should flag 'Worth a closer look'")

    def test_homepage_earlier_draws_dedup_filter(self):
        # the list below the hero must drop the featured latest by NUMERIC id
        self.assertRegex(read("index"), r'filter\(function \(it\) \{ return Number\(it\.id\) !== latestId; \}\)')


if __name__ == "__main__":
    unittest.main()
