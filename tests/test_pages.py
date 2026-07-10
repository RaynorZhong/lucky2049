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
        # the list below the hero must drop the featured latest by NUMERIC id (whitespace-tolerant)
        self.assertRegex(read("index"),
                         r'\.filter\(\s*function\s*\(\s*it\s*\)\s*\{\s*return\s+Number\(\s*it\.id\s*\)\s*!==\s*latestId\s*;?\s*\}')

    def test_randomness_die_caption_serial_and_block_range(self):
        # each die carries a caption: a stable serial (the window index #k) over the block range it
        # was derived from -- a handle for locating a die. Lock both the CSS hooks and the JS wiring.
        html = read("randomness")
        for cls in (".die-cell", ".die-cap", ".die-no", ".die-blk"):
            self.assertIn(cls + " ", html, "missing caption style " + cls)
        # lockDie writes the serial and the block range into the caption
        self.assertRegex(html, r'\.die-no"\)\.textContent\s*=\s*"#"\s*\+\s*k')
        self.assertRegex(html, r'\.die-blk"\)\.textContent\s*=\s*"#"\s*\+\s*lo\s*\+\s*"–#"\s*\+\s*hi')
        # the serial is also in the per-die aria-label (for screen-reader locating) and the summary line
        self.assertRegex(html, r'aria-label",\s*"Die #"\s*\+\s*k')
        self.assertRegex(html, r'newest die #"\s*\+\s*k0')

    def test_randomness_coin_caption_block_number(self):
        # each coin carries its block number as a caption (a handle for locating it).
        html = read("randomness")
        for cls in (".coin-cell", ".coin-cap", ".coin-no"):
            self.assertIn(cls + " ", html, "missing caption style " + cls)
        # lockCoin writes the block number into the caption
        self.assertRegex(html, r'\.coin-no"\)\.textContent\s*=\s*"#"\s*\+\s*h')
        # perspective must live on .coin-cell, NOT on .coins -- on .coins it skips the coin
        # (now a grandchild) and flattens the 3-D flip. Lock both halves of that fix.
        cell_rule = re.search(r'\.coin-cell\s*\{([^}]*)\}', html)
        self.assertIsNotNone(cell_rule, ".coin-cell rule not found")
        self.assertIn("perspective", cell_rule.group(1), ".coin-cell must carry the 3-D perspective")
        coins_rule = re.search(r'\.coins\s*\{([^}]*)\}', html)
        self.assertIsNotNone(coins_rule, ".coins rule not found")
        self.assertNotIn("perspective", coins_rule.group(1),
                         ".coins must NOT set perspective (it would flatten the wrapped coin's flip)")


def read_css():
    with open(os.path.join(REPO_ROOT, "static", "style.css")) as f:
        return f.read()


class TestAudit5Guards(unittest.TestCase):
    """Locks for the audit-5 cleanup sweep (CSS-cascade and queue-hygiene fixes)."""

    def test_home_reduced_motion_override_after_animated_dot_rules(self):
        # equal specificity: the prefers-reduced-motion override only wins if it comes LATER
        # in the cascade than BOTH animated-dot rules it neutralizes.
        html = read("index")
        override = html.find("prefers-reduced-motion")
        self.assertGreater(override, html.find(".nd-dot {"), "reduce-motion block must follow .nd-dot")
        self.assertGreater(override, html.find(".src-health .dot {"), "reduce-motion block must follow .src-health .dot")

    def test_live_dot_idiom_shared_and_single(self):
        # one breathing idiom for every liveness dot: keyframes live in style.css, both pages
        # reference it, and the old randomness box-shadow ripple is gone.
        self.assertIn("@keyframes live-breathe", read_css())
        self.assertIn("animation: live-breathe", read("index"))
        rnd = read("randomness")
        self.assertIn("animation: live-breathe", rnd)
        self.assertNotIn("@keyframes pulse", rnd)

    def test_trend_header_band_outspecifies_zone_transparency(self):
        # .ttab .z0 (0,2,0) beats .ttab thead th (0,1,2), so the header band needs its own
        # thead-scoped re-assert (0,2,1) AFTER the transparent rule.
        html = read("trend")
        transparent = html.find(".ttab .z0, .ttab .z1, .ttab .cb { background: transparent")
        reassert = html.find(".ttab thead .z0, .ttab thead .z1, .ttab thead .cb { background: var(--surface-2)")
        self.assertGreater(transparent, -1, "zone transparency rule not found")
        self.assertGreater(reassert, transparent, "thead band re-assert must exist AFTER the transparent rule")

    def test_trend_cells_immune_to_global_mobile_font_rule(self):
        # style.css @768px `th, td { font-size: 0.9rem }` is a direct element declaration that
        # beats inheritance; the matrix cells must re-couple to table.ttab via font-size: inherit
        # (and the header pins its own size, since the global th 0.72rem no longer reaches it).
        html = read("trend")
        cells = re.search(r'\.ttab th, \.ttab td \{([^}]*)\}', html)
        self.assertIsNotNone(cells, ".ttab th/.ttab td rule not found")
        self.assertIn("font-size: inherit", cells.group(1))
        thead = re.search(r'\.ttab thead th \{([^}]*)\}', html)
        self.assertIsNotNone(thead, ".ttab thead th rule not found")
        self.assertIn("font-size:", thead.group(1), "header must pin its own font-size")

    def test_stats_bars_use_ball_tokens(self):
        # the frequency bars are ball-coloured surfaces: they must ride the shared flat tokens,
        # not a second hardcoded (pre-flat) gradient.
        html = read("stats")
        self.assertRegex(html, r'\.chart \.bar \{[^}]*var\(--ball-ltc\)')
        self.assertRegex(html, r'\.chart \.bar\.back \{[^}]*var\(--ball-btc\)')
        self.assertNotIn("linear-gradient(180deg, #ffb24d", html)

    def test_randomness_trim_purges_stale_queue_heads(self):
        # a long-hidden tab pauses the rAF drain while the poll keeps trimming blocks; the trim
        # must also purge queue entries whose blocks were evicted or tick() derives from
        # undefined on re-focus and the reveal loop dies.
        html = read("randomness")
        self.assertRegex(html, r'coinQ\s*=\s*coinQ\.filter\(\s*function\s*\(\s*h\s*\)\s*\{\s*return\s+blocks\[h\]\s*;?\s*\}\s*\)')
        self.assertRegex(html, r'dieQ\s*=\s*dieQ\.filter\(\s*windowComplete\s*\)')

    def test_note_class_defined_once_in_shared_css(self):
        # .note is used by verify AND randomness header leads; the single definition lives in
        # style.css so the two pages can't silently diverge again.
        self.assertRegex(read_css(), r'\.note \{[^}]*font-size: 0\.9rem')
        for p in ("verify", "randomness"):
            self.assertIsNone(re.search(r'\.note \{', read(p)), "local .note rule on " + p + ".html")


if __name__ == "__main__":
    unittest.main()
