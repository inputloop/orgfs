#!/usr/bin/env python3
# Copyright 2026 Corwin Brust <corwin@bru.st> and Contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The stack page as the site's proof of concept: the machine that built the page, read at build time.

The tools are stubs the tests write (so their versions are known and unlike the example's), and the
build's own facts (run name, commit, log) are made up here and checked against what the page says.
Python 3 standard library only.
"""
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
ROOT = T.ROOT_DIR
DECL = os.path.join(ROOT, "examples", "licensing.d")
EXAMPLE = os.path.join(ROOT, "examples", "stack.d")
COMMIT = "0123456789abcdef0123456789abcdef01234567"
RUN = "20200101T000000Z-0123456789ab"            # the stamp is long before the atoms below were read
LOG_OK = ("orgfs-ci 20200101T000000Z building " + COMMIT + "\n== check\nsh -n bin/orgfs\n....\n"
          "org_check: ok example (licensing.d: source form, expanded form, manifest)\norg_check: ok who (28 links resolved by Org)\n"
          "org_check: OK (321 assertions; Org 9.99, Emacs 99.1)\n== prove-red\norg_check: FAILED (a planted fault)\n== site\n")
CLAIM = "This page was built and tested by the machine described below; it read its own tools while building it."


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def text_of(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def site(td, name, stack, run=RUN, commit=COMMIT, log=None, live=True):
    out = os.path.join(td, name)
    args = ["site", DECL, out, "--stack", stack]
    if live:
        args += ["--live", "--build-run", run, "--build-commit", commit]
        if log:
            args += ["--build-log", log]
    rc, so, err = orgfs(*args)
    return rc, so, err, out


class LiveBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.td = cls._td.name
        cls.s1 = S.Stubs(cls.td, "s1", S.V1, S.LIC1)
        cls.d1 = os.path.join(cls.td, "stack")
        rc, cls.out1, err = orgfs("stack", cls.d1, env=cls.s1.env())
        assert rc == 0, err
        cls.log = os.path.join(cls.td, "run.log")
        with open(cls.log, "w") as f:
            f.write(LOG_OK)
        rc, cls.so, cls.err, cls.out = site(cls.td, "live", cls.d1, log=cls.log)
        assert rc == 0, cls.err
        cls.html = read(os.path.join(cls.out, "stack.html"))
        cls.made = read(os.path.join(cls.out, "made.html"))
        cls.home = read(os.path.join(cls.out, "index.html"))

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()


class UsesTheStackItWasGiven(LiveBase):
    def test_the_page_shows_the_stack_it_was_given_and_nothing_of_the_example(self):
        t = text_of(self.html)
        for want in ("git version 9.9.9.stub", "Python 3.99.1", "GNU Emacs 99.1", "nginx version: nginx/1.99.0", "GNU Make 9.8", "rsync version 9.7.1"):
            self.assertIn(want, t)
        ex = text_of(read(os.path.join(EXAMPLE, "tool-git.org"))) + read(os.path.join(EXAMPLE, "tool-emacs.org"))
        self.assertIn("git version 2.43.0", ex)                       # the example is a different machine's
        for stranger in ("git version 2.43.0", "1:2.43.0-1ubuntu7.4"):
            self.assertNotIn(stranger, t)
        # and the atoms the page links to are the ones it was given
        self.assertEqual(read(os.path.join(self.out, "stack", "tool-git.org")), read(os.path.join(self.d1, "tool-git.org")))

    def test_the_page_opens_with_the_claim_and_links_the_exact_run_and_commit(self):
        first = re.search(r"<h1>.*?</h1>\s*(<p>.*?</p>)", self.html, re.S).group(1)
        self.assertIn(CLAIM, text_of(first))
        self.assertIn('<a href="/ci/runs/%s.log">' % RUN, first)
        self.assertIn('<a href="https://github.com/inputloop/orgfs/commit/%s">%s</a>' % (COMMIT, COMMIT[:12]), first)
        self.assertEqual(RUN.split("-")[1], COMMIT[:12])

    def test_every_tool_reads_its_version_with_the_command_and_both_debian_links_or_none(self):
        h = self.html
        self.assertIn('<h2 id="tool-git">git</h2>', h)
        self.assertRegex(h, r"git version 9\.9\.9\.stub<br>as it reported itself, by <code>git --version</code>")
        self.assertIn("1:9.9.9.stub-1stub1; source package git; upstream version 9.9.9.stub", h)
        # the stub host names no Debian release, so there is no link to a release that is not its own
        self.assertNotIn("packages.debian.org", h)
        self.assertIn("The host does not name a Debian release", h)
        self.assertEqual(len(re.findall(r'<h2 id="tool-', h)), 9)
        self.assertRegex(h, r"(?s)id=\"tool-org\".*?<code>emacs -Q --batch --eval")

    def test_debian_links_are_plain_links_for_the_release_the_host_names(self):
        d = os.path.join(self.td, "stack-deb")
        shutil.copytree(self.d1, d)
        for n in os.listdir(d):
            if n.startswith("tool-"):
                p = os.path.join(d, n)
                with open(p, encoding="utf-8") as f:
                    x = f.read()
                x = re.sub(r"^:READ_ENV: .*$", ":READ_ENV: Linux 6.1.0; Debian GNU/Linux 12 (bookworm)", x, flags=re.M)
                with open(p, "w", encoding="utf-8") as f:
                    f.write(x)
        rc, so, err, out = site(self.td, "deb", d, log=self.log)
        self.assertEqual(rc, 0, err)
        h = read(os.path.join(out, "stack.html"))
        self.assertIn('<a href="https://packages.debian.org/bookworm/git">', h)
        self.assertIn('<a href="https://tracker.debian.org/pkg/git">', h)
        self.assertIn('<a href="https://tracker.debian.org/pkg/make-dfsg">', h)          # the source package, not the binary's name
        self.assertIn('<a href="https://packages.debian.org/bookworm/emacs-common">', h)   # org lives in emacs-common
        self.assertNotRegex(h, r'(?i)<(script|form|iframe|img)\b|\ssrc=')

    def test_licence_confidence_history_corrections_and_gaps_are_shown_per_tool(self):
        h = self.html
        self.assertIn("Which commits make this version distinct: not read yet (needs the upstream tree).", h)
        self.assertIn("<dt>Confidence</dt>", h)
        self.assertIn("History</dt><dd>1 guess:", h)
        self.assertIn('<a href="#correct">How to offer one</a>', h)
        self.assertIn('id="correct"', h)
        # nginx's stub copyright file has no License: line: a gap, said plainly
        self.assertRegex(h, r"(?s)id=\"tool-nginx\".*?The licence the package states: not read")

    def test_the_example_site_still_has_no_live_claim(self):
        rc, so, err, out = site(self.td, "example", EXAMPLE, live=False)
        self.assertEqual(rc, 0, err)
        h = read(os.path.join(out, "stack.html"))
        self.assertNotIn(CLAIM, h)
        self.assertIn("They were not read by the machine that built this page", h)
        self.assertNotIn("/ci/runs/", h)
        self.assertNotIn("SEEN_HERE", h)
        self.assertNotIn("The machine that built this", read(os.path.join(out, "index.html")).replace("See the machine that built this page", ""))
        self.assertIn("See the tools of an example machine", read(os.path.join(out, "index.html")))


class TheClaimHoldsOnlyWhenItIsTrue(LiveBase):
    def not_live(self, name, d, why, **kw):
        rc, so, err, out = site(self.td, name, d, log=self.log, **kw)
        self.assertEqual(rc, 0, err)                              # a page that cannot claim it is still a page, and the build stays green
        self.assertIn("NOT live", err)
        self.assertIn(why, err)
        h = read(os.path.join(out, "stack.html"))
        self.assertNotIn(CLAIM, h)
        self.assertNotIn("/ci/runs/" + RUN, h)
        self.assertIn("did not get one", h)
        self.assertNotIn("SEEN_HERE", h)
        self.assertIn("not on the machine that built this page", h)
        made = read(os.path.join(out, "made.html"))
        self.assertNotIn("Emacs was <code>", made)
        self.assertIn("so the Emacs and Org versions are not stated here", made)
        idx = read(os.path.join(out, "index.html"))
        self.assertNotIn("See the machine that built this page", idx)
        return h

    def edited(self, name, fn):
        d = os.path.join(self.td, name)
        shutil.copytree(self.d1, d)
        fn(d)
        return d

    def test_an_atom_read_on_another_machine_is_never_shown_as_read_here(self):
        def f(d):
            p = os.path.join(d, "tool-git.org")
            x = read(p).replace(":READ_ON: " + subprocess.run(["uname", "-n"], capture_output=True, text=True).stdout.strip(), ":READ_ON: some-other-host")
            with open(p, "w") as fh:
                fh.write(x)
        self.not_live("elsewhere", self.edited("stack-else", f), "tool-git.org was read on 'some-other-host'")

    def test_a_carried_atom_is_never_shown_as_read_here(self):
        seed = os.path.join(self.td, "seed.org")
        rc, so, err = orgfs("compose", self.d1, "--out", seed)
        self.assertEqual(rc, 0, err)
        d = os.path.join(self.td, "stack-carried")
        rc, so, err = orgfs("stack", d, "--seed", seed, env=self.s1.env())
        self.assertEqual(rc, 0, err)
        self.assertIn(":CARRIED:", read(os.path.join(d, "tool-python3.org")).split("\n")[0])
        self.not_live("carried", d, "a carried atom came from a seed")

    def test_an_atom_older_than_the_build_is_not_this_builds_reading(self):
        # the stack step did not run in this build: the atoms are last build's
        rc, so, err, out = site(self.td, "stale", self.d1, run="20990101T000000Z-0123456789ab", log=self.log)
        self.assertEqual(rc, 0, err)
        self.assertIn("NOT live", err)
        self.assertIn("before this build began", err)
        self.assertNotIn(CLAIM, read(os.path.join(out, "stack.html")))

    def test_the_run_link_must_match_the_commit_and_must_be_well_formed(self):
        for name, run, commit, why in (
            ("badhash", "20200101T000000Z-ffffffffffff", COMMIT, "does not name the commit"),
            ("shortcommit", RUN, COMMIT[:12], "no 40-character commit"),
            ("badrun", "build-7", COMMIT, "not STAMP-HASH12"),
            ("norun", "", COMMIT, "no build run was named"),
        ):
            rc, so, err, out = site(self.td, name, self.d1, run=run, commit=commit, log=self.log)
            self.assertEqual(rc, 0, err)
            self.assertIn("NOT live", err, name)
            self.assertIn(why, err, name)
            h = read(os.path.join(out, "stack.html"))
            self.assertNotIn(CLAIM, h, name)
            self.assertNotIn("/ci/runs/", h, name)

    def test_no_live_flag_no_claim_whatever_the_stack(self):
        rc, so, err, out = site(self.td, "nolive", self.d1, live=False)
        self.assertEqual(rc, 0, err)
        self.assertNotIn(CLAIM, read(os.path.join(out, "stack.html")))


class HowThisWasMade(LiveBase):
    def test_the_real_versions_and_the_real_org_check_line_of_this_build(self):
        m = self.made
        self.assertIn("Emacs was <code>GNU Emacs 99.1</code> and Org was <code>9.99</code>", m)
        self.assertIn("<code>org_check: OK (321 assertions; Org 9.99, Emacs 99.1)</code>", m)
        self.assertIn('<a href="/ci/runs/%s.log">the log of that run</a>' % RUN, m)
        self.assertNotIn("FAILED (a planted fault)", m)            # that line is from the prove-red phase, not the check
        self.assertNotIn("org_check: ok example", m)               # a progress line is not the summary the check ends with

    def test_without_the_line_the_pattern_stays_and_the_reason_is_given(self):
        for name, content, why in (("nolog", None, "no log of this build was named"),
                                   ("noline", "== check\nall fine\norg_check: ok example (progress lines are not the summary)\n== prove-red\norg_check: OK (1 assertions; Org 1, Emacs 1)\n", "has no org_check line yet"),
                                   ("unreadable", "MISSING", "cannot be read here")):
            log = os.path.join(self.td, name + ".log")
            if content and content != "MISSING":
                with open(log, "w") as f:
                    f.write(content)
            args = ["site", DECL, os.path.join(self.td, name), "--stack", self.d1, "--live", "--build-run", RUN, "--build-commit", COMMIT]
            if content:
                args += ["--build-log", log]
            rc, so, err = orgfs(*args)
            self.assertEqual(rc, 0, err)
            m = read(os.path.join(self.td, name, "made.html"))
            self.assertIn("Emacs was <code>GNU Emacs 99.1</code>", m, name)
            self.assertIn("so the form stays <code>org_check: OK (N assertions; Org X, Emacs Y)</code>", m, name)
            self.assertIn(why, m, name)
            self.assertNotRegex(m, r"<code>org_check: OK \(\d", name)

    def test_the_home_page_has_one_prominent_link_and_keeps_its_story(self):
        i = self.home
        self.assertIn('<p class="go"><a href="stack.html">See the machine that built this page, and how sure we are about each licence it runs under.</a></p>', i)
        self.assertEqual(i.count('href="stack.html"'), 2)         # the nav and that link
        self.assertIn("<h2>An example</h2>", i)
        self.assertLess(i.index("<h2>An example</h2>"), i.index("See the machine that built this page"))
        self.assertLess(i.index("See the machine that built this page"), i.index("<h2>What happens to a file</h2>"))
        self.assertIn('<li><a href="stack.html">The machine that built this</a></li>', i)

    def test_the_page_has_its_status_line_and_a_not_checked_section(self):
        self.assertEqual(self.html.count('class="status"'), 1)
        self.assertRegex(self.html, r"<h2>Not checked</h2>")
        self.assertIn("Which commits make each version distinct", self.html)


class OneStackAcrossBuilds(unittest.TestCase):
    """The stack directory is kept between builds: guesses grow only when inputs change; corrections stay."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.td = cls._td.name
        cls.s1 = S.Stubs(cls.td, "s1", S.V1, S.LIC1)
        cls.d = os.path.join(cls.td, "stack")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def guesses(self):
        out = []
        for root, _, files in os.walk(os.path.join(self.d, "guesses")):
            out += [os.path.join(root, f) for f in files]
        return sorted(out)

    def test_a_second_build_with_unchanged_tools_appends_no_guess_and_keeps_the_corrections(self):
        rc, so1, err = orgfs("stack", self.d, env=self.s1.env())
        self.assertEqual(rc, 0, err)
        g1 = self.guesses()
        self.assertGreaterEqual(len(g1), 5)
        # a correction is proposed and approved between builds
        rc, so, err = orgfs("stack", "correct", "git", "--says", "GPL-2 only", "--by", "Alice", "--why", "the stub says so", "--stack", self.d)
        self.assertEqual(rc, 0, err)
        cid = sorted(os.listdir(os.path.join(self.d, "corrections", "git")))[0][:-4]
        rc, so, err = orgfs("stack", "approve", "git/" + cid, "--by", "Bob", "--stack", self.d)
        self.assertEqual(rc, 0, err)
        before = {p: read(p) for p in g1}
        corr = read(os.path.join(self.d, "corrections", "git", cid + ".org"))
        # the next build: same tools, same files
        rc, so2, err = orgfs("stack", self.d, env=self.s1.env())
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.guesses(), g1)                                  # no guess appended
        self.assertEqual({p: read(p) for p in g1}, before)                    # and none rewritten (no NEXT written)
        self.assertIn("unchanged (same inputs and method", so2)
        self.assertNotIn("wrote guesses/", so2)
        self.assertEqual(read(os.path.join(self.d, "corrections", "git", cid + ".org")), corr)
        self.assertIn(":IN_EFFECT: approved correction", read(os.path.join(self.d, "tool-git.org")))
        # the next build: git's copyright file changed, so one guess is appended and linked, and only that one
        with open(os.path.join(self.s1.doc, "git", "copyright"), "a") as f:
            f.write("\nFiles: c\nLicense: MIT-ish\n text of a new stanza that is not in any reference text\n")
        rc, so3, err = orgfs("stack", self.d, env=self.s1.env())
        self.assertEqual(rc, 0, err)
        g3 = self.guesses()
        self.assertEqual(len(g3), len(g1) + 1)
        new = [p for p in g3 if p not in g1][0]
        self.assertIn("/guesses/git/", new)
        self.assertIn(":PREVIOUS: [[file:", read(new))
        self.assertIn(":NEXT: [[file:" + os.path.basename(new), "\n".join(read(p) for p in g1 if "/guesses/git/" in p))
        self.assertEqual(read(os.path.join(self.d, "corrections", "git", cid + ".org")), corr)


class AnUnreadableToolIsAGapNotAFailure(unittest.TestCase):
    def test_tools_that_cannot_be_read_give_gap_records_and_every_step_stays_green(self):
        with tempfile.TemporaryDirectory() as td:
            s = S.Stubs(td, "s1", S.V1, S.LIC1)
            os.unlink(os.path.join(s.bin, "make"))
            os.unlink(os.path.join(s.bin, "rsync"))
            farm = os.path.join(td, "farm")
            os.makedirs(farm)
            need = "sh awk gawk mawk sed sort cut tr head tail wc cat date uname mktemp rm mkdir cp mv cmp dd grep find basename dirname readlink sha256sum ls printf expr tee dash bash busybox".split()
            for n in need:
                w = shutil.which(n)
                if w and not os.path.exists(os.path.join(farm, n)):
                    os.symlink(w, os.path.join(farm, n))
            env = {"PATH": os.pathsep.join([s.bin, farm]), "ORGFS_DOC": s.doc, "ORGFS_COMMON_LICENSES": os.path.join(td, "cl")}
            os.makedirs(env["ORGFS_COMMON_LICENSES"])
            d = os.path.join(td, "stack")
            rc, so, err = orgfs("stack", d, env=env)
            self.assertEqual(rc, 0, err)                                      # not a failure
            a = S.atoms_of(d)
            for t in ("make", "rsync"):
                self.assertEqual(S.props(a[t])["PRESENT"], "no", t)
                self.assertIn("stack-%s-absent" % t, S.gap_ids(a[t]), t)
                self.assertNotIn("VERSION", S.props(a[t]), t)
            self.assertEqual(S.props(a["git"])["PRESENT"], "yes")             # the others are read as before
            log = os.path.join(td, "run.log")
            with open(log, "w") as f:
                f.write(LOG_OK)
            rc, so, err, out = site(td, "site", d, log=log)
            self.assertEqual(rc, 0, err)
            h = read(os.path.join(out, "stack.html"))
            self.assertIn(CLAIM, h)                                           # a tool that is not there does not unmake the page
            t = text_of(h)
            self.assertRegex(t, r"make, its version, package and licence: not read\. Why: make is not on PATH on this host")
            self.assertRegex(t, r"rsync, its version, package and licence: not read\. Why: rsync is not on PATH on this host")
            self.assertIn("Which commits make this version distinct: not read yet (needs the upstream tree).", t)


class TheMakefile(unittest.TestCase):
    def dry(self, *args):
        if shutil.which("make") is None:
            self.skipTest("NOT CHECKED: no make here")
        p = subprocess.run(["make", "-n", "-C", ROOT, "site"] + list(args), capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def test_make_site_uses_the_example_and_make_site_stack_uses_the_stack_it_is_given(self):
        plain = self.dry()
        self.assertIn("--stack examples/stack.d", plain)
        self.assertEqual(plain.count("--live"), 1)                  # in the branch that needs STACK, which is empty here
        self.assertIn('if [ -n "" ]', plain)
        live = self.dry("STACK=/c4/ci/orgfs/stack", "BUILD_RUN=" + RUN, "BUILD_COMMIT=" + COMMIT, "BUILD_LOG=/p/run.log")
        self.assertIn('--stack "/c4/ci/orgfs/stack" --live --build-run "%s" --build-commit "%s" --build-log "/p/run.log"' % (RUN, COMMIT), live)
        # the tests do not name STACK, so they keep using the example
        with open(os.path.join(ROOT, "Makefile")) as f:
            mk = f.read()
        self.assertRegex(mk, r"(?m)^STACK \?=\s*$")


if __name__ == "__main__":
    unittest.main()
