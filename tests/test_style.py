#!/usr/bin/env python3
# Copyright 2026 Corwin Brust <corwin@bru.st> and Contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The site's look: contrast (WCAG AA), measure, scale, focus, print, and the demo page.

Colours are read out of the generated orgfs.css and the contrast ratios are computed
here from the WCAG 2.x formula, so a palette change that fails AA turns this red.
"""
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_orgfs as T  # noqa: E402
import test_views as V  # noqa: E402

orgfs = T.orgfs


def lum(h):
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def ratio(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def tokens(block):
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\b", block))


class StyleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.out = os.path.join(cls._td.name, "site")
        rc, so, err = orgfs("site", os.path.join(T.ROOT_DIR, "examples", "licensing.org"), cls.out)
        assert rc == 0, err
        with open(os.path.join(cls.out, "orgfs.css")) as f:
            cls.css = f.read()
        cls.pages = {}
        for n in ("index.html", "gaps.html", "licensing.html", "rights.html"):
            with open(os.path.join(cls.out, n), encoding="utf-8") as f:
                cls.pages[n] = f.read()

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def schemes(self):
        light = tokens(re.search(r":root\s*\{(.*?)\n\}", self.css, re.S).group(1))
        dark_block = re.search(r"prefers-color-scheme: dark\)\s*\{\s*:root\s*\{(.*?)\}", self.css, re.S).group(1)
        dark = dict(light)
        dark.update(tokens(dark_block))
        pm = re.search(r"@media print\s*\{\s*:root\s*\{(.*?)\}", self.css, re.S).group(1)
        prn = dict(light)
        prn.update(tokens(pm))
        return {"light": light, "dark": dark, "print": prn}

    def test_text_pairs_meet_wcag_aa(self):
        for name, t in self.schemes().items():
            for fg in ("fg", "mut", "link", "visited"):
                for bg in ("bg", "band"):
                    self.assertGreaterEqual(ratio(t[fg], t[bg]), 4.5, (name, fg, bg, ratio(t[fg], t[bg])))

    def test_borders_and_focus_ring_meet_3_to_1(self):
        for name, t in self.schemes().items():
            for bg in ("bg", "band"):
                self.assertGreaterEqual(ratio(t["rule"], t[bg]), 3.0, (name, "rule", bg))
                self.assertGreaterEqual(ratio(t["fg"], t[bg]), 3.0, (name, "focus ring", bg))

    def test_both_colour_schemes_are_declared(self):
        self.assertIn("color-scheme: light dark", self.css)
        self.assertIn("prefers-color-scheme: dark", self.css)

    def test_focus_is_visible_and_never_removed(self):
        m = re.search(r"[^{}]*:focus-visible[^{}]*\{([^}]*)\}", self.css)
        self.assertTrue(m)
        w = re.search(r"outline:\s*(\d+)px\s+solid", m.group(1))
        self.assertTrue(w, m.group(1))
        self.assertGreaterEqual(int(w.group(1)), 2)
        self.assertIsNone(re.search(r"outline:\s*(none|0)\b", self.css))
        self.assertIn(".skip:focus", self.css)

    def test_line_length_scale_and_rhythm(self):
        ch = int(re.search(r"--measure:\s*(\d+)ch", self.css).group(1))
        self.assertTrue(45 <= ch <= 80, ch)
        self.assertIn("max-width: var(--measure)", self.css)
        steps = re.findall(r"--s(-?\d+):\s*([0-9.]+)rem", self.css)
        self.assertGreaterEqual(len(steps), 5)
        vals = [float(v) for _, v in sorted(steps, key=lambda x: int(x[0]))]
        self.assertEqual(vals, sorted(vals))
        self.assertGreaterEqual(float(re.search(r"line-height:\s*([0-9.]+);\s*\n?\s*margin: 0 auto", self.css, re.S).group(1)), 1.5)
        self.assertIn("--sp:", self.css)

    def test_tables_are_readable(self):
        for needle in ("caption", "tbody tr:nth-child(even)", "thead th", "overflow-x: auto", "tbody th"):
            self.assertIn(needle, self.css, needle)
        for n, html in self.pages.items():
            for tbl in re.findall(r"<table>.*?</table>", html, re.S):
                self.assertIn("<caption>", tbl, n)
                self.assertIn('<th scope="col">', tbl, n)

    def test_there_is_a_print_stylesheet(self):
        m = re.search(r"@media print\s*\{(.*)\n\}\s*$", self.css, re.S)
        self.assertTrue(m)
        body = m.group(1)
        self.assertRegex(body, r"nav[^{]*\{[^}]*display:\s*none")
        self.assertIn("attr(href)", body)
        self.assertIn("break-inside: avoid", body)
        self.assertIn("pre-wrap", body)
        self.assertIn("@page", body)

    def test_small_screens_are_handled(self):
        self.assertIn("@media (max-width:", self.css)
        self.assertIn('name="viewport"', self.pages["index.html"])

    def test_every_page_has_a_status_line_and_a_not_checked_section(self):
        for n, html in self.pages.items():
            self.assertEqual(html.count('class="status"'), 1, n)
            self.assertRegex(html, r"<h2>Not checked</h2>", n)
            self.assertTrue(html.index('class="status"') < html.index("<h2>"), n)
        idx = self.pages["index.html"]
        for h in ("What you get", "What was asked", "What was observed", "What was judged"):
            self.assertIn("<h2>%s</h2>" % h, idx)
        self.assertTrue(idx.index("What you get") < idx.index("What was asked") < idx.index("What was observed") < idx.index("What was judged"))

    def test_judged_items_carry_a_falsifier(self):
        for n in ("index.html", "gaps.html"):
            html = self.pages[n]
            judged = re.search(r"<h2>What was judged</h2>(.*?)<h2>", html, re.S).group(1)
            items = re.findall(r"<li>(.*?)</li>", judged, re.S)
            self.assertGreaterEqual(len(items), 2, n)
            for it in items:
                self.assertRegex(it, r"[Ww]ould be wrong", it)

    def test_quotes_are_short_and_dated(self):
        idx = self.pages["index.html"]
        self.assertIn("2026-11-06", idx)
        for q in re.findall(r"<blockquote>(.*?)</blockquote>", idx, re.S):
            self.assertLess(len(re.sub(r"<[^>]+>", "", q)), 700)

    # ---------------------------------------------------------- the demo page
    def test_the_gaps_page_shows_every_gap_of_the_demo_view(self):
        html = self.pages["gaps.html"]
        with open(os.path.join(self.out, "view-visitor.org")) as f:
            view = f.read()
        ids = V.gap_ids(view)
        self.assertGreater(len(ids), 10)
        table = re.search(r"<tbody>(.*?)</tbody>", html, re.S).group(1)
        shown = set(re.findall(r"<th scope=\"row\"><code>([^<]+)</code>", table))
        self.assertEqual(shown, ids)
        for k in ("member-root", "objects", "slice-bytes", "spoke"):
            self.assertIn("<td>%s</td>" % k, table)
        self.assertIn("ring-notes/member-root/demo1", html)
        self.assertIn("Declared at", html)

    def test_the_demo_view_on_the_site_leaks_nothing_and_has_no_temp_path(self):
        with open(os.path.join(self.out, "view-visitor.org")) as f:
            view = f.read()
        self.assertNotIn(tempfile.gettempdir() + "/orgfs", view)
        self.assertIn("/demo/", view)
        self.assertNotIn("/demo/private", view)
        self.assertNotIn("/demo/vault", view)
        for h in self.pages["gaps.html"].split("<pre"):
            self.assertNotIn("/demo/private/", h)
            self.assertNotIn("/demo/vault/", h)

    def test_a_private_spokes_roots_are_not_on_the_index_and_a_sealed_one_is_only_a_name(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        w = V.World(d.name)
        out = os.path.join(d.name, "site")
        rc, _, err = orgfs("site", w.decl, out)
        self.assertEqual(rc, 0, err)
        with open(os.path.join(out, "index.html")) as f:
            idx = f.read()
        for (s, h), root in w.roots.items():
            if s != "pub":
                self.assertNotIn(root, idx, (s, h))
        self.assertIn("Spoke sealed: withheld", idx)
        self.assertNotIn("h5", re.sub(r"[0-9a-f]{64}", "", idx))
        self.assertFalse(os.path.exists(os.path.join(out, "declaration.org")))
        self.assertIn("Spoke pub", idx)
        self.assertIn(w.roots[("pub", "h1")], idx)


if __name__ == "__main__":
    unittest.main(verbosity=1)
