#!/usr/bin/env python3
# Copyright 2026 Corwin Brust <corwin@bru.st> and Bryant Frankford (INPUTLOOP)
# SPDX-License-Identifier: GPL-3.0-or-later
"""`orgfs stack`, licence confidence: the score, the bands, the history of guesses, the corrections.

The measure is worked out again here, in Python, from the description in the README (words, 4-word
shingles, containment, the longest shared run by a plain dynamic programme over words, the greedy
components), so a fault in the awk cannot hide behind itself.  The reference texts are invented, with
words that cannot occur by chance in each other.  Python 3 standard library only.
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_orgfs as T  # noqa: E402
import test_stack as S  # noqa: E402

orgfs = T.orgfs
K, MINW = 4, 12
BANDS = (4, 9, 17)


# ---------------------------------------------------------------- the measure, again, in Python
def words(text):
    return re.findall(r"[a-z0-9]+", text.lower().replace("'", ""))


def covered(stanza, ref):
    """Indexes of the stanza's words that lie inside a K-word run the reference also holds."""
    shingles = {tuple(ref[i:i + K]) for i in range(len(ref) - K + 1)}
    out = set()
    for j in range(len(stanza) - K + 1):
        if tuple(stanza[j:j + K]) in shingles:
            out.update(range(j, j + K))
    return out


def longest_run(a, b):
    best = 0
    prev = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


def band(unc, n):
    if unc * 100 <= BANDS[0] * n:
        return "high"
    if unc * 100 <= BANDS[1] * n:
        return "moderate"
    if unc * 100 <= BANDS[2] * n:
        return "low"
    return "gap"


def components(stanza, refs):
    """Greedy: the reference adding most uncovered words, up to 4, each after the first adding at least MINW."""
    cov = {name: covered(stanza, r) for name, r in refs.items()}
    have, out = set(), []
    while len(out) < 4:
        best, bg = None, 0
        for name in sorted(refs):
            g = len(cov[name] - have)
            if g > bg:
                best, bg = name, g
        if best is None or bg < (1 if not out else MINW):
            break
        out.append((best, bg))
        have |= cov[best]
    return out, have


# ---------------------------------------------------------------- the files
def ref_words(prefix, n=150):
    return ["license"] + ["%s%d" % (prefix, i) for i in range(1, n)]


REFS = {"Alpha": ref_words("alpha"), "Bravo": ref_words("bravo"), "Charlie": ref_words("charlie")}


def wrap(ws, per=9):
    return "\n".join(" ".join(ws[i:i + per]) for i in range(0, len(ws), per)) + "\n"


def make_cl(td):
    cl = os.path.join(td, "common-licenses")
    os.makedirs(cl)
    for n, ws in REFS.items():
        with open(os.path.join(cl, n), "w") as f:
            f.write(wrap(ws))
    os.symlink("Alpha", os.path.join(cl, "A"))          # a symlink is an alias, not another reference
    return cl


def stanza_with_islands(u, total=100, ref=None):
    """TOTAL words of which exactly U are foreign: runs of the reference separated by single foreign words.
    The reference starts with license, the vocabulary word that makes a free-text block a stanza."""
    ref = ref or REFS["Alpha"]
    nrun = u + 1
    covered_words = total - u
    sizes = [covered_words // nrun + (1 if i < covered_words % nrun else 0) for i in range(nrun)]
    assert min(sizes) >= K, sizes
    out, pos, z = [], 0, 0
    for i, s in enumerate(sizes):
        out += ref[pos:pos + s]
        pos += s
        if i < nrun - 1:
            z += 1
            out.append("zzz%d" % z)
    assert len(out) == total
    return out


def props_of(block):
    out = {}
    for l in block.split("\n"):
        m = re.match(r"^:([A-Z_0-9]+):(?: (.*))?$", l)
        if m:
            out[m.group(1)] = m.group(2) or ""
        elif l == ":END:":
            break
    return out


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def guess_stanzas(text):
    """[(heading, props)] for the '** stanza' headings of a guess atom, by plain reading."""
    parts = re.split(r"^(?=\*\* )", text, flags=re.M)
    out = []
    for p in parts[1:]:
        h = p.split("\n", 1)[0][3:]
        out.append((h, props_of(p.split("\n", 1)[1])))
    return out


def guess_files(d, pkg):
    g = os.path.join(d, "guesses", pkg)
    return sorted(os.path.join(g, n) for n in os.listdir(g)) if os.path.isdir(g) else []


class Host:
    """A stub host (S.Stubs) whose copyright files are the ones given, plus the invented reference texts."""

    def __init__(self, td, name, lic):
        self.td = td
        self.cl = make_cl(td) if not os.path.isdir(os.path.join(td, "common-licenses")) else os.path.join(td, "common-licenses")
        base = dict(S.LIC1)
        base.update(lic)
        self.stubs = S.Stubs(td, name, S.V1, base)
        self.doc = self.stubs.doc

    def env(self):
        e = self.stubs.env()
        e["ORGFS_COMMON_LICENSES"] = self.cl
        return e

    def stack(self, d, *more):
        rc, so, err = orgfs("stack", d, *more, env=self.env())
        assert rc == 0, err
        return so

    def write(self, pkg, text):
        os.makedirs(os.path.join(self.doc, pkg), exist_ok=True)
        with open(os.path.join(self.doc, pkg, "copyright"), "w") as f:
            f.write(text)


def free(*blocks):
    return "This package was put together by somebody.\n\n" + "\n\n".join(blocks) + "\n"


class ScoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.td = cls._td.name
        notice = wrap(REFS["Alpha"][0:50])                                   # 50 words, all of them in Alpha
        cls.altered = list(REFS["Bravo"][0:50])
        cls.altered[25] = "changed"                                          # one word changed: 4 shingles broken
        two = wrap(REFS["Alpha"][0:40] + REFS["Bravo"][1:41])                # one stanza holding two licences
        cls.texts = {
            "git": free("Copyright 1999 Somebody <s@example.org>\n" + notice, wrap(cls.altered), wrap(REFS["Charlie"][0:55])),
            "emacs": free(two),
            "python3": "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\n\nFiles: *\nLicense: Expat\n\nFiles: x\nLicense: Home-grown\n\nLicense: Home-grown\n"
                       + "".join(" " + l + "\n" for l in wrap(REFS["Bravo"][0:70]).splitlines())
                       + "\nLicense: Nothing-here\n",
        }
        cls.h = Host(cls.td, "h1", cls.texts)
        cls.d = os.path.join(cls.td, "stack")
        cls.out = cls.h.stack(cls.d)

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def stanzas(self, pkg):
        (g,) = guess_files(self.d, pkg)
        return read(g), guess_stanzas(read(g))

    def test_a_free_text_file_is_scored_by_stanza_and_agrees_with_the_independent_measure(self):
        text, st = self.stanzas("git")
        self.assertEqual(props_of(text.split("\n", 1)[1])["FORMAT"], "free-text")
        ws = {h: p for h, p in st}
        # the holder line is set aside; the notice is Alpha's own words, so nothing is outside it
        s1 = ws["stanza 1"]
        n1 = 50
        self.assertEqual((s1["WORDS"], s1["HOLDER_LINES_SET_ASIDE"]), (str(n1), "1"))
        self.assertEqual(s1["BAND"], "high")
        self.assertEqual(s1["COVERED_WORDS"], str(n1))
        self.assertEqual(s1["BEST"], "Alpha")
        self.assertIn("alone 100.0%", s1["COMPONENT_1"])
        # the second stanza: Bravo with one word changed, so the changed word and the three before and after it ... by the measure
        sw = list(self.altered)
        cov = covered(sw, REFS["Bravo"])
        s2 = ws["stanza 2"]
        self.assertEqual(s2["WORDS"], str(len(sw)))
        self.assertEqual(int(s2["COVERED_WORDS"]), len(cov))
        self.assertEqual(s2["BAND"], band(len(sw) - len(cov), len(sw)))
        self.assertIn("longest shared run %d words" % longest_run(sw, REFS["Bravo"]), s2["COMPONENT_1"])
        self.assertEqual(int(s2["NOT_IN_ANY_REFERENCE_WORDS"]), len(sw) - len(cov))

    def test_each_stanza_is_its_own_guess_never_collapsed_into_one(self):
        text, st = self.stanzas("git")
        best = [p.get("BEST") for h, p in st]
        self.assertEqual(best, ["Alpha", "Bravo", "Charlie"])
        self.assertEqual(props_of(text.split("\n", 1)[1])["STANZAS_SCORED"], "3")
        self.assertEqual(props_of(text.split("\n", 1)[1])["SUMMARY"].count("stanza "), 3)

    def test_the_prose_that_is_no_licence_is_set_aside_and_counted(self):
        text, st = self.stanzas("git")
        self.assertEqual(props_of(text.split("\n", 1)[1])["PROSE_BLOCKS_SET_ASIDE"], "0")
        h = Host(self.td, "h-prose", {"git": free("The author thanks everybody who sent a patch over the long years of this project.")})
        d = os.path.join(self.td, "stack-prose")
        h.stack(d)
        (g,) = guess_files(d, "git")
        p = props_of(read(g).split("\n", 1)[1])
        self.assertEqual((p["STANZAS_SCORED"], p["PROSE_BLOCKS_SET_ASIDE"]), ("0", "1"))
        self.assertEqual(p["SHORT_BLOCKS_SET_ASIDE"], "1")

    def test_one_stanza_holding_two_licences_has_two_components(self):
        text, st = self.stanzas("emacs")
        (h, p), = st
        sw = REFS["Alpha"][0:40] + REFS["Bravo"][1:41]
        comps, have = components(sw, REFS)
        self.assertEqual([c for c, _ in comps], ["Alpha", "Bravo"])
        self.assertEqual(p["BEST"], comps[0][0])
        self.assertIn(p["COMPONENT_1"].split(";")[0], ("Alpha", "Bravo"))
        self.assertTrue(p["COMPONENT_2"].startswith(comps[1][0]))
        self.assertNotIn("COMPONENT_3", p)
        self.assertEqual(int(p["COVERED_WORDS"]), len(have))

    def test_a_dep5_paragraph_with_a_nonstandard_name_is_scored_and_a_standard_one_is_not(self):
        text, st = self.stanzas("python3")
        head = props_of(text.split("\n", 1)[1])
        self.assertEqual(head["FORMAT"], "dep5")
        self.assertEqual(head["STANDARD_NAMES"], "Expat")
        names = {p.get("NAME"): p for h, p in st}
        self.assertNotIn("Expat", names)
        hg = names["Home-grown"]
        self.assertEqual(hg["BEST"], "Bravo")
        self.assertEqual(hg["NAME_AGREES"], "no")                          # it says Home-grown; the text is Bravo's
        # a name with no text of its own is a gap of its own kind
        self.assertEqual(names["Nothing-here"]["GAP_KIND"], "name-without-text")
        self.assertEqual(names["Nothing-here"]["BAND"], "gap")

    def test_a_paragraph_other_packages_state_under_a_standard_name_is_a_reference_too(self):
        zl = ["zeta%d" % i for i in range(1, 31)]
        body = "".join(" " + l + "\n" for l in wrap(zl).splitlines())
        dep = "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\n\nFiles: *\nLicense: Zlib\n" + body
        mine = "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\n\nFiles: *\nLicense: Home-made\n\nLicense: Home-made\n" + body
        h = Host(self.td, "h-dep", {"dpa": dep, "dpb": dep, "python3": mine})
        d = os.path.join(self.td, "stack-dep")
        h.stack(d)
        (g,) = guess_files(d, "python3")
        (hh, p), = guess_stanzas(read(g))
        self.assertEqual(p["BEST"], "Zlib (DEP-5 paragraph)")
        self.assertEqual(p["BAND"], "high")
        refs = [n for n in os.listdir(os.path.join(d, "references")) if n.startswith("Zlib-")]
        self.assertEqual(len(refs), 1)
        rp = props_of(read(os.path.join(d, "references", refs[0])).split("\n", 1)[1])
        self.assertIn("License: field of a DEP-5 paragraph", rp["NAME_FROM"])
        self.assertIn("held by 2 packages", rp["NAME_FROM"])
        self.assertEqual(rp["SHA256"], hashlib.sha256(("\n".join(zl) + "\n").encode()).hexdigest())
        # a reference that only this package holds is not used to score this package
        h2 = Host(self.td, "h-dep2", {"python3": dep})
        d2 = os.path.join(self.td, "stack-dep2")
        h2.stack(d2)
        self.assertEqual(os.listdir(os.path.join(d2, "references")) if os.path.isdir(os.path.join(d2, "references")) else [], [n for n in os.listdir(os.path.join(d2, "references"))])

    def test_the_gap_has_a_kind_and_lists_the_words_that_matched_no_reference(self):
        text, st = self.stanzas("git")
        s3 = dict(st)["stanza 3"]
        self.assertEqual(s3["BAND"], "high")
        h = Host(self.td, "h-gap", {"git": free(wrap(["license"] + ["unknownword%d" % i for i in range(40)]))})
        d = os.path.join(self.td, "stack-gap")
        h.stack(d)
        (g,) = guess_files(d, "git")
        (hh, p), = guess_stanzas(read(g))
        self.assertEqual((p["BAND"], p["GAP_KIND"]), ("gap", "no-reference"))
        self.assertIn("words 1-41: license unknownword0 unknownword1", read(g))

    def test_a_pointer_to_a_reference_by_path_is_its_own_kind_of_gap(self):
        h = Host(self.td, "h-ptr", {"git": free("See the full text of the license in /usr/share/common-licenses/Alpha if you like to read such things.")})
        d = os.path.join(self.td, "stack-ptr")
        h.stack(d)
        (g,) = guess_files(d, "git")
        (hh, p), = guess_stanzas(read(g))
        self.assertEqual(p["CITES"], "Alpha")
        self.assertEqual(p["GAP_KIND"], "points-at-reference")

    def test_the_references_are_atoms_with_name_path_sha256_and_where_the_name_came_from(self):
        refs = os.path.join(self.d, "references")
        names = sorted(os.listdir(refs))
        self.assertTrue(any(n.startswith("Alpha-") for n in names))
        self.assertFalse(any(n.startswith("A-") for n in names))            # the symlink is an alias
        a = read(os.path.join(refs, [n for n in names if n.startswith("Alpha-")][0]))
        p = props_of(a.split("\n", 1)[1])
        self.assertEqual(p["NAME"], "Alpha")
        self.assertEqual(p["PATH"], os.path.join(self.h.cl, "Alpha"))
        with open(p["PATH"], "rb") as f:
            self.assertEqual(p["SHA256"], hashlib.sha256(f.read()).hexdigest())
        self.assertIn("common-licenses", p["NAME_FROM"])
        self.assertEqual(p["ALIASES"], "A")
        self.assertTrue(os.path.basename(refs) == "references" and a.startswith("* reference Alpha"))
        # a reference text taken from License: paragraphs of DEP-5 files on the host

    def test_references_are_not_written_twice(self):
        refs = os.path.join(self.d, "references")
        before = {n: os.stat(os.path.join(refs, n)).st_mtime_ns for n in os.listdir(refs)}
        self.h.stack(self.d)
        after = {n: os.stat(os.path.join(refs, n)).st_mtime_ns for n in os.listdir(refs)}
        self.assertEqual(before, after)


class BandTests(unittest.TestCase):
    """A band is decided by integers: a gap of exactly 4, 9 or 17 per cent belongs to the lower band."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.td = cls._td.name

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    runs = 0

    def run_island(self, u):
        st = stanza_with_islands(u)
        BandTests.runs += 1
        h = Host(self.td, "hb%d" % BandTests.runs, {"git": free(wrap(st))})
        d = os.path.join(self.td, "stack-b%d" % BandTests.runs)
        h.stack(d)
        (g,) = guess_files(d, "git")
        (hh, p), = guess_stanzas(read(g))
        return st, p

    def test_the_edges_are_exact_and_agree_with_the_independent_measure(self):
        want = {0: "high", 4: "high", 5: "moderate", 9: "moderate", 10: "low", 17: "low", 18: "gap"}
        for u, b in want.items():
            st, p = self.run_island(u)
            cov = covered(st, REFS["Alpha"])
            self.assertEqual(len(st) - len(cov), u, u)                          # the fixture is what it says
            self.assertEqual(p["WORDS"], "100")
            self.assertEqual(p["OUTSIDE_THE_MATCH_WORDS"], str(u), u)
            self.assertEqual(p["BAND"], b, "a gap of %d of 100 words" % u)
            self.assertEqual(p["BAND"], band(u, 100))

    def test_below_the_lowest_band_it_stays_a_gap_and_says_what_kind(self):
        st, p = self.run_island(18)
        self.assertEqual((p["BAND"], p["GAP_KIND"]), ("gap", "partial-text"))
        self.assertIn("82.0% of the words matched", p["CONFIDENCE"])


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = self._td.name
        self.h = Host(self.td, "hh", {"git": free(wrap(["license"] + REFS["Alpha"][1:60]))})
        self.d = os.path.join(self.td, "stack")
        self.out1 = self.h.stack(self.d)

    def tearDown(self):
        self._td.cleanup()

    def chain(self, pkg="git"):
        files = guess_files(self.d, pkg)
        link = lambda t, k: (re.search(r"^:%s: (.*)$" % k, t, re.M) or [None, None])[1]
        nodes = {}
        for f in files:
            t = read(f)
            nodes[os.path.basename(f)] = (link(t, "PREVIOUS"), link(t, "NEXT"), t)
        return nodes

    def test_the_first_guess_has_no_previous_and_says_so(self):
        n = self.chain()
        self.assertEqual(len(n), 1)
        (prev, nxt, t), = n.values()
        self.assertEqual((prev, nxt), ("none", None))
        self.assertRegex(list(n)[0], r"^20\d{6}T\d{6}Z(-\d+)?-shingle4-containment-v1\.org$")
        self.assertRegex(self.out1, r"stack: guess for git: wrote guesses/git/")

    def test_a_rerun_with_the_same_inputs_and_method_writes_nothing_and_says_so(self):
        before = {f: read(f) for f in guess_files(self.d, "git")}
        so = self.h.stack(self.d)
        self.assertRegex(so, r"stack: guess for git unchanged \(same inputs and method as guesses/git/.*\): nothing written")
        self.assertEqual({f: read(f) for f in guess_files(self.d, "git")}, before)

    def test_a_changed_input_appends_a_guess_linked_both_ways_and_changes_nothing_else_in_the_old_one(self):
        old_f = guess_files(self.d, "git")[0]
        old = read(old_f)
        self.h.write("git", free(wrap(["license"] + REFS["Alpha"][1:60] + ["added", "words"])))
        so = self.h.stack(self.d)
        self.assertRegex(so, r"stack: guess for git changed: wrote guesses/git/.* after ")
        n = self.chain()
        self.assertEqual(len(n), 2)
        a, b = [os.path.basename(f) for f in guess_files(self.d, "git")]
        self.assertEqual(n[b][0], "[[file:%s]]" % a)                    # PREVIOUS on the new one
        self.assertEqual(n[a][1], "[[file:%s]]" % b)                    # NEXT written on the old one
        self.assertEqual(n[a][0], "none")
        self.assertIsNone(n[b][1])
        # the old guess is the old guess, with one line more
        new_old = read(old_f)
        self.assertEqual(new_old.replace(":NEXT: [[file:%s]]\n" % b, ""), old)
        # walk the chain from the end back to the start and from the start to the end
        walk = [b]
        while n[walk[-1]][0] != "none":
            walk.append(re.match(r"\[\[file:(.*)\]\]", n[walk[-1]][0]).group(1))
        self.assertEqual(walk, [b, a])
        fwd = [a]
        while n[fwd[-1]][1]:
            fwd.append(re.match(r"\[\[file:(.*)\]\]", n[fwd[-1]][1]).group(1))
        self.assertEqual(fwd, [a, b])
        self.assertNotEqual(props_of(read(os.path.join(self.d, "guesses", "git", a)).split("\n", 1)[1])["INPUTS_KEY"],
                            props_of(read(os.path.join(self.d, "guesses", "git", b)).split("\n", 1)[1])["INPUTS_KEY"])

    def test_the_guess_records_its_inputs_by_hash(self):
        (f,) = guess_files(self.d, "git")
        p = props_of(read(f).split("\n", 1)[1])
        with open(os.path.join(self.h.doc, "git", "copyright"), "rb") as fh:
            self.assertEqual(p["FILE_SHA256"], hashlib.sha256(fh.read()).hexdigest())
        self.assertRegex(p["REFERENCE_SET_SHA256"], r"^[0-9a-f]{64}$")
        self.assertEqual((p["METHOD"], p["METHOD_VERSION"], p["SHINGLE_WORDS"]), ("shingle4-containment", "1", "4"))
        self.assertIn("house numbers, invented", p["BAND_PERCENTS"])

    def test_a_changed_reference_set_is_a_changed_input(self):
        shutil.rmtree(os.path.join(self.h.cl, "Charlie"), ignore_errors=True)
        os.unlink(os.path.join(self.h.cl, "Charlie"))
        so = self.h.stack(self.d)
        self.assertIn("changed", so)
        self.assertEqual(len(guess_files(self.d, "git")), 2)

    def test_the_tool_atom_shows_the_current_guess_and_links_the_history(self):
        a = read(os.path.join(self.d, "tool-git.org"))
        self.assertIn("** licence guess", a)
        cur = os.path.basename(guess_files(self.d, "git")[0])
        self.assertIn(":GUESS_CURRENT: guesses/git/%s" % cur, a)
        self.assertIn(":GUESS_HISTORY: guesses/git/ (1 guess)", a)
        self.h.write("git", free(wrap(["license"] + REFS["Alpha"][1:60] + ["added", "words"])))
        self.h.stack(self.d)
        a = read(os.path.join(self.d, "tool-git.org"))
        self.assertIn("(2 guesses)", a)
        new = os.path.basename(guess_files(self.d, "git")[-1])
        self.assertIn(":GUESS_CURRENT: guesses/git/%s" % new, a)
        self.assertIn("guesses/git/%s" % cur, a)                         # the old one is listed in the history
        # and the composed stack is still something orgfs accepts, with the guesses outside it
        rc, so, err = orgfs("compose", self.d, "--out", os.path.join(self.td, "c.org"))
        self.assertEqual(rc, 0, err)
        self.assertNotIn("shingle4-containment-v1.org", read(os.path.join(self.td, "c.org")).split("#+NAME: orgfs-manifest")[-1])

    def test_a_forked_history_is_refused(self):
        f = guess_files(self.d, "git")[0]
        shutil.copy(f, os.path.join(os.path.dirname(f), "20300101T000000Z-shingle4-containment-v1.org"))
        self.h.write("git", free(wrap(["license"] + REFS["Alpha"][1:30])))
        rc, so, err = orgfs("stack", self.d, env=self.h.env())
        self.assertNotEqual(rc, 0)
        self.assertIn("forked", err)


class CorrectionTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = self._td.name
        self.h = Host(self.td, "hc", {"git": free(wrap(["license"] + REFS["Alpha"][1:60]))})
        self.d = os.path.join(self.td, "stack")
        self.h.stack(self.d)

    def tearDown(self):
        self._td.cleanup()

    def correct(self, by="Alice", says="Bravo", why="the file says so", pkg="git", extra=()):
        return orgfs("stack", "correct", pkg, "--says", says, "--by", by, "--why", why, "--stack", self.d, *extra, env=self.h.env())

    def cfile(self):
        g = os.path.join(self.d, "corrections", "git")
        return [os.path.join(g, n) for n in sorted(os.listdir(g))]

    def test_a_correction_is_proposed_and_does_not_outrank_the_guess(self):
        rc, so, err = self.correct()
        self.assertEqual(rc, 0, err)
        (f,) = self.cfile()
        p = props_of(read(f).split("\n", 1)[1])
        self.assertEqual((p["STATE"], p["SAYS"], p["PROPOSED_BY"], p["PACKAGE"]), ("proposed", "Bravo", "Alice", "git"))
        self.assertRegex(p["PROPOSED_AT"], r"^20\d\d-\d\d-\d\dT")
        self.assertIn("[[file:../../guesses/git/", p["GUESS_THEN"])
        a = read(os.path.join(self.d, "tool-git.org"))
        self.assertIn(":IN_EFFECT: machine guess", a)
        self.assertIn(":CORRECTIONS_PROPOSED: 1", a)
        self.assertIn("waiting for someone else to approve it", a)

    def test_the_proposer_cannot_approve_their_own_correction(self):
        self.correct(by="Alice Example")
        (f,) = self.cfile()
        cid = os.path.basename(f)[:-4]
        for who in ("Alice Example", "alice example", "  ALICE   Example "):
            rc, so, err = orgfs("stack", "approve", "git/" + cid, "--by", who, "--stack", self.d)
            self.assertNotEqual(rc, 0, who)
            self.assertIn("refused", err)
        self.assertEqual(props_of(read(f).split("\n", 1)[1])["STATE"], "proposed")
        self.assertNotIn("APPROVED_BY", read(f))

    def test_someone_else_approves_it_and_it_outranks_the_guess_which_stays_visible(self):
        self.correct()
        (f,) = self.cfile()
        cid = os.path.basename(f)[:-4]
        rc, so, err = orgfs("stack", "approve", "git/" + cid, "--by", "Bob", "--stack", self.d)
        self.assertEqual(rc, 0, err)
        p = props_of(read(f).split("\n", 1)[1])
        self.assertEqual((p["STATE"], p["APPROVED_BY"]), ("approved", "Bob"))
        self.assertRegex(p["APPROVED_AT"], r"^20\d\d-\d\d-\d\dT")
        a = read(os.path.join(self.d, "tool-git.org"))
        self.assertIn(":IN_EFFECT: approved correction", a)
        self.assertIn("the licence in effect for package git is Bravo", a)
        # the guess is still there, with its confidence, beside it
        self.assertIn("the machine guess (kept, and outranked by the correction above)", a)
        self.assertRegex(a, r"stanza 1: Alpha \(high\)")
        self.assertTrue(guess_files(self.d, "git"))                    # and never deleted
        # an ID alone, or the path, name the same correction; approving twice changes nothing
        before = read(f)
        rc, so, err = orgfs("stack", "approve", cid, "--by", "Carol", "--stack", self.d)
        self.assertEqual(rc, 0, err)
        self.assertIn("already approved", so)
        self.assertEqual(read(f), before)

    def test_the_composed_stack_with_a_correction_is_still_a_stack_orgfs_accepts(self):
        self.correct()
        (f,) = self.cfile()
        orgfs("stack", "approve", "git/" + os.path.basename(f)[:-4], "--by", "Bob", "--stack", self.d)
        rc, so, err = orgfs("compose", self.d, "--out", os.path.join(self.td, "c.org"))
        self.assertEqual(rc, 0, err)
        rc, so, err = orgfs("routes", os.path.join(self.td, "c.org"))
        self.assertEqual(rc, 0, err)

    def test_what_is_refused(self):
        for kw in ({"by": ""}, {"says": ""}, {"why": ""}, {"by": "A\tB"}, {"why": "two\nlines"}, {"says": "x" * 501}):
            rc, so, err = self.correct(**kw)
            self.assertNotEqual(rc, 0, kw)
        rc, so, err = self.correct(pkg="no-such-package")
        self.assertNotEqual(rc, 0)
        self.assertIn("no tool atom", err)
        rc, so, err = self.correct(pkg="../etc")
        self.assertNotEqual(rc, 0)
        rc, so, err = orgfs("stack", "approve", "git/nope", "--by", "Bob", "--stack", self.d)
        self.assertNotEqual(rc, 0)
        self.assertFalse(os.path.exists(os.path.join(self.d, "corrections", "git")) and self.cfile())

    def test_a_later_stack_run_keeps_the_corrections_and_shows_them(self):
        self.correct()
        (f,) = self.cfile()
        orgfs("stack", "approve", "git/" + os.path.basename(f)[:-4], "--by", "Bob", "--stack", self.d)
        self.h.stack(self.d)
        a = read(os.path.join(self.d, "tool-git.org"))
        self.assertIn(":IN_EFFECT: approved correction", a)
        self.assertEqual(len(self.cfile()), 1)


class SiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.td = cls._td.name
        cls.h = Host(cls.td, "hs", {"git": free(wrap(["license"] + REFS["Alpha"][1:60]), wrap(["license"] + ["unknownword%d" % i for i in range(30)]))})
        cls.d = os.path.join(cls.td, "stack")
        cls.h.stack(cls.d)
        orgfs("stack", "correct", "git", "--says", "Bravo", "--by", "Alice", "--why", "reasons", "--stack", cls.d)
        g = os.path.join(cls.d, "corrections", "git")
        cls.cid = sorted(os.listdir(g))[0][:-4]
        orgfs("stack", "approve", "git/" + cls.cid, "--by", "Bob", "--stack", cls.d)
        cls.site = os.path.join(cls.td, "site")
        rc, so, err = orgfs("site", os.path.join(T.ROOT_DIR, "examples", "licensing.d"), cls.site, "--stack", cls.d)
        assert rc == 0, err
        cls.html = read(os.path.join(cls.site, "stack.html"))

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_each_scored_tool_shows_confidence_in_number_and_words_with_the_history_and_corrections(self):
        h = self.html
        self.assertIn('<h2 id="tool-git">git</h2>', h)
        self.assertIn("100.0% of the words matched", h)
        self.assertIn("high confidence", h)
        self.assertIn("a gap (no-reference)", h)
        self.assertIn("shingle4-containment", h)
        self.assertIn("1 guess:", h)
        self.assertIn("Each links to the one before and after it", h)
        self.assertIn("Alice says Bravo", h)

    def test_the_approved_correction_is_in_effect_and_the_guess_is_not_hidden(self):
        h = self.html
        self.assertIn("<strong>Bravo</strong>, an approved correction proposed by Alice and approved by Bob", h)
        self.assertIn("Machine guess (kept)", h)
        self.assertIn("Stanzas of the copyright file of git, scored", h)

    def test_how_to_offer_a_correction_is_a_plain_instruction_not_a_form(self):
        h = self.html
        self.assertIn("Offer a correction", h)
        self.assertIn("github.com/inputloop/orgfs/issues", h)
        self.assertIn("corwin@bru.st", h)
        self.assertNotRegex(h, r"(?i)<(form|input|button|textarea|script)\b")
        self.assertNotRegex(h, r"(?i)\sstyle=")
        self.assertNotRegex(h, r"(?i)\sonclick=")

    def test_every_link_into_the_stack_leads_to_a_file_that_is_there(self):
        links = re.findall(r'href="(stack/[^"]+)"', self.html)
        self.assertTrue(links)
        for l in links:
            self.assertTrue(os.path.isfile(os.path.join(self.site, l)), l)
        for p in ("guesses/git", "corrections/git", "references"):
            self.assertTrue(os.listdir(os.path.join(self.site, "stack", p)))

    def test_nothing_is_loaded_from_another_host_and_the_nav_names_the_page(self):
        h = self.html
        self.assertNotRegex(h, r'(?i)\b(src|srcset)="')
        self.assertEqual(re.findall(r'<link [^>]*href="([^"]+)"', h), ["orgfs.css"])
        self.assertIn('<a href="stack.html" aria-current="page">', h)
        self.assertIn("default-src 'none'", h)

    def test_no_stack_given_no_stack_page(self):
        s = os.path.join(self.td, "site0")
        rc, so, err = orgfs("site", os.path.join(T.ROOT_DIR, "examples", "licensing.d"), s)
        self.assertEqual(rc, 0, err)
        self.assertFalse(os.path.exists(os.path.join(s, "stack.html")))
        self.assertNotIn("stack.html", read(os.path.join(s, "index.html")))


class NoOutsideCallsTests(unittest.TestCase):
    def test_no_network_or_model_call_is_made_while_scoring(self):
        with tempfile.TemporaryDirectory() as td:
            h = Host(td, "hn", {"git": free(wrap(["license"] + REFS["Alpha"][1:60]))})
            for n in ("ollama", "llama-cli", "llm", "claude", "openai"):
                h.stubs.write(n, 'echo "%s $*" >> %s; exit 1' % (n, h.stubs.log))
            d = os.path.join(td, "stack")
            h.stack(d)
            h.write("git", free(wrap(["license"] + REFS["Bravo"][1:40])))
            h.stack(d)
            calls = S.read(h.stubs.log).splitlines()
            self.assertTrue(calls)
            for c in calls:
                self.assertRegex(c, r"^dpkg-query (-S|-W) ", c)

    def test_the_scoring_code_names_no_command_that_reaches_out(self):
        with open(T.BIN) as f:
            src = f.read()
        a = src.index("# ------------------------------------------------------------------ licence confidence")
        b = src.index("# The document `orgfs who` writes, as HTML.")
        code = "\n".join(l for l in src[a:b].split("\n") if not l.lstrip().startswith("#"))
        bad = re.search(r"\b(curl|wget|ssh|rsync|ftp|nc|ncat|ollama|llama|openai|anthropic|claude)\b\s|https?://|\bgit (fetch|pull|clone)\b|apt-get ", code)
        self.assertIsNone(bad, bad and bad.group(0))


if __name__ == "__main__":
    unittest.main()
