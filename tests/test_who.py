#!/usr/bin/env python3
# Copyright 2026 Corwin Brust <corwin@bru.st> and Bryant Frankford (INPUTLOOP)
# SPDX-License-Identifier: GPL-3.0-or-later
"""`orgfs who`: who worked on a repository, over time.  Python 3 standard library only.

The expected facts are taken from git by this file's own calls (git log, git diff-tree), and the
identifiers are re-derived here from the rule in the README, so a fault in the tool's reading or
naming cannot hide behind itself.  The links are read back by a reader written here, not by the tool.
"""
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_orgfs as T  # noqa: E402

orgfs = T.orgfs
ZW = "\u200b"
GIT = shutil.which("git")


def enc(s):
    """The identifier rule: bytes outside [A-Za-z0-9.-] are written _XX (hex), so it is reversible."""
    return "".join(chr(b) if (chr(b).isalnum() and b < 128) or chr(b) in ".-" else "_%02x" % b for b in s.encode())


def git(repo, *args, env=None, check=True):
    e = dict(os.environ)
    e.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
              "HOME": repo, "GIT_TERMINAL_PROMPT": "0"})
    if env:
        e.update(env)
    p = subprocess.run([GIT, "-C", repo] + list(args), capture_output=True, env=e)
    if check and p.returncode != 0:
        raise RuntimeError("git %s: %s" % (args, p.stderr.decode()))
    return p.stdout.decode()


def commit(repo, name, email, date, subject, files=None, allow_empty=False):
    for path, text in (files or {}).items():
        full = os.path.join(repo, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "a", encoding="utf-8") as f:
            f.write(text)
    git(repo, "add", "-A")
    env = {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_AUTHOR_DATE": date,
           "GIT_COMMITTER_NAME": "Committer Person", "GIT_COMMITTER_EMAIL": "committer@example.org", "GIT_COMMITTER_DATE": date}
    git(repo, "commit", "-q", "-m", subject, *(["--allow-empty"] if allow_empty else []), env=env)


def make_repo(td):
    """Two spellings of one name, an empty commit, a merge, odd paths, hostile text."""
    repo = os.path.join(td, "repo")
    os.makedirs(repo)
    git(repo, "init", "-q", "-b", "main")
    commit(repo, "Alice Example", "alice@example.org", "2026-01-10T09:00:00+00:00", "first", {"a.txt": "a\n", "dir/b.txt": "b\n"})
    commit(repo, "Bob Example", "bob@example.org", "2026-02-01T10:00:00+01:00", "second", {"a.txt": "more\n", "sp ace.txt": "s\n", "\u00fcn\u00ef.txt": "u\n"})
    commit(repo, "alice example", "alice@example.org", "2026-03-01T11:00:00-06:00", "third: the other spelling", {"dir/b.txt": "again\n"})
    commit(repo, "Alice Example", "alice@example.org", "2026-03-05T12:00:00+00:00", "an empty commit", allow_empty=True)
    commit(repo, "dependabot[bot]", "bot@example.org", "2026-04-01T13:00:00+00:00", "fix [[evil]] link <<target>> {{{macro}}}", {"weird [[x]].txt": "w\n"})
    commit(repo, "Bob Example", "BOB@EXAMPLE.ORG", "2026-04-02T13:00:00+00:00", "* not a heading", {"#+BEGIN_SRC.txt": "x\n"})
    git(repo, "checkout", "-q", "-b", "side")
    commit(repo, "Bob Example", "bob@example.org", "2026-04-03T08:00:00+00:00", "on a side branch", {"side.txt": "s\n"})
    git(repo, "checkout", "-q", "main")
    commit(repo, "Alice Example", "alice@example.org", "2026-04-04T08:00:00+00:00", "on main meanwhile", {"main.txt": "m\n"})
    env = {"GIT_AUTHOR_NAME": "Alice Example", "GIT_AUTHOR_EMAIL": "alice@example.org", "GIT_AUTHOR_DATE": "2026-04-05T08:00:00+00:00",
           "GIT_COMMITTER_NAME": "Committer Person", "GIT_COMMITTER_EMAIL": "committer@example.org", "GIT_COMMITTER_DATE": "2026-04-05T08:00:00+00:00"}
    git(repo, "merge", "-q", "--no-ff", "-m", "merge the side branch", "side", env=env)
    return repo


HEAD_RE = re.compile(r"^(\*+) (.*)$")
LINK_RE = re.compile(r"\[\[#([^\]]*)\]\[(.*?)\]\]")


def sections(doc):
    """Headings of a who document: list of dicts {level, title, id, props, body(lines)}, and the id->heading map."""
    out, cur, indr = [], None, False
    for line in doc.split("\n"):
        m = HEAD_RE.match(line)
        if m and not (cur is not None and cur.get("inblock")):
            cur = {"level": len(m.group(1)), "title": m.group(2), "id": None, "props": {}, "body": [], "inblock": False}
            out.append(cur)
            indr = False
            continue
        if cur is None:
            continue
        if line == ":PROPERTIES:" and not cur["body"]:
            indr = True
            continue
        if indr:
            if line == ":END:":
                indr = False
            else:
                k, _, v = line[1:].partition(":")
                cur["props"][k] = v.strip() if v.startswith(" ") else v
                if k == "CUSTOM_ID":
                    cur["id"] = cur["props"][k]
            continue
        if line.startswith("#+BEGIN_"):
            cur["inblock"] = True
        elif line.startswith("#+END_"):
            cur["inblock"] = False
        cur["body"].append(line)
    return out


def graph(doc):
    """(ids by kind, edges) where an edge is (source id, target id) for every [[#id][..]] link under a heading with an id."""
    secs = sections(doc)
    ids = {}
    for s in secs:
        if s["id"]:
            assert s["id"] not in ids, "duplicate id " + s["id"]
            ids[s["id"]] = s
    edges = []
    for s in secs:
        if s["id"]:
            for line in s["body"]:
                for tid, _lab in LINK_RE.findall(line):
                    edges.append((s["id"], tid))
    return ids, edges


def kind(i):
    return i.split("-", 1)[0]


class WhoBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if GIT is None:
            raise unittest.SkipTest("NOT CHECKED: no git here")
        cls._td = tempfile.TemporaryDirectory()
        cls.td = cls._td.name
        cls.repo = make_repo(cls.td)
        rc, cls.doc, err = orgfs("who", cls.repo)
        assert rc == 0, err

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def who(self, *args, repo=None):
        rc, so, err = orgfs("who", repo or self.repo, *args)
        self.assertEqual(rc, 0, err)
        return so


class WhoTests(WhoBase):
    def expected(self):
        """Commits as git itself lists them: hash -> (name, email, date, set of paths)."""
        out = {}
        for h in git(self.repo, "rev-list", "HEAD").split():
            name, email, date, subj = git(self.repo, "log", "-1", "--no-mailmap", "--date=short", "--format=%an%x1f%ae%x1f%ad%x1f%s", h).strip("\n").split("\x1f", 3)
            files = set(f for f in git(self.repo, "-c", "core.quotepath=off", "diff-tree", "--root", "--no-renames", "-r", "--name-only", "--no-commit-id", h).split("\n") if f)
            out[h] = (name, email, date, subj, files)
        return out

    def test_every_contributor_path_and_commit_is_a_target_linked_both_ways(self):
        ids, edges = graph(self.doc)
        exp = self.expected()
        self.assertEqual(sum(1 for i in ids if kind(i) == "commit"), len(exp))
        eset = set(edges)
        for (a, b) in edges:
            self.assertIn(b, ids, "link to %s resolves to nothing" % b)
            if kind(a) != kind(b):
                self.assertIn((b, a), eset, "%s links %s but %s does not link back" % (a, b, b))
        # from any of the three you reach the other two
        for h, (name, email, date, subj, files) in exp.items():
            cid = [i for i in ids if i.startswith("commit-") and h.startswith(i[len("commit-"):])]
            self.assertEqual(len(cid), 1, h)
            cid = cid[0]
            out = [b for a, b in edges if a == cid]
            people = [b for b in out if kind(b) == "person"]
            paths = [b for b in out if kind(b) == "path"]
            self.assertEqual(people, ["person-" + enc("%s <%s>" % (name, email))])
            self.assertEqual(sorted(paths), sorted("path-" + enc(f) for f in files))
            for p in people + paths:
                self.assertIn((p, cid), eset)
        # a person reaches each of their paths, and each of those paths reaches the person
        for a, b in edges:
            if kind(a) == "person" and kind(b) == "path":
                self.assertIn((b, a), eset)

    def test_identities_are_exactly_as_the_commits_give_them_and_never_merged(self):
        secs = [s for s in sections(self.doc) if s["id"] and s["id"].startswith("person-")]
        got = sorted((s["props"]["NAME"], s["props"]["EMAIL"]) for s in secs)
        want = sorted(set((n, e) for (n, e, *_r) in self.expected().values()))
        self.assertEqual(got, want)
        self.assertIn(("Alice Example", "alice@example.org"), got)
        self.assertIn(("alice example", "alice@example.org"), got)       # the second spelling stays
        self.assertIn(("Bob Example", "BOB@EXAMPLE.ORG"), got)           # the case of an email is kept
        self.assertEqual(len(got), 5)
        # and a sentence says so, with the count of headings that share an address
        self.assertRegex(self.doc, r"two spellings are two headings")
        self.assertRegex(self.doc, r"Observed: 2 headings share one email address with another heading \(1 address in all\)")

    def test_a_mailmap_file_is_not_read(self):
        with open(os.path.join(self.repo, ".mailmap"), "w") as f:
            f.write("Alice Example <alice@example.org> alice example <alice@example.org>\n")
        try:
            doc = self.who()
        finally:
            os.remove(os.path.join(self.repo, ".mailmap"))
        names = [s["props"]["NAME"] for s in sections(doc) if s["id"] and s["id"].startswith("person-")]
        self.assertIn("alice example", names)
        self.assertEqual(len(names), 5)

    def test_the_document_opens_with_a_story_of_counted_facts(self):
        secs = sections(self.doc)
        self.assertEqual(secs[0]["title"], "About this document")
        about = "\n".join(secs[0]["body"])
        exp = self.expected()
        self.assertRegex(about, r"Observed: This document was written by orgfs who \(orgfs [0-9][^)]*\) on 20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z")
        self.assertIn("It made no network call", about)
        self.assertIn("git -C", about)
        self.assertIn("log --no-color --no-renames --name-only", about)
        self.assertIn("It holds %d commits by 5 contributors" % len(exp), about)
        paths = set().union(*(v[4] for v in exp.values()))
        self.assertIn("touching %d paths" % len(paths), about)
        self.assertIn("earliest author date among them is 2026-01-10 and the latest is 2026-04-05", about)
        self.assertRegex(about, r"Judged: Nothing\.")
        self.assertEqual(secs[0]["props"]["COMMITS"], str(len(exp)))
        # the sections follow, in this order
        self.assertEqual([s["title"] for s in secs if s["level"] == 1], ["About this document", "Contributors", "Paths", "Commits"])

    def test_observed_sentences_are_marked_and_no_judgement_is_made(self):
        body = [l for s in sections(self.doc) for l in s["body"] if l and not l.startswith(("-", " ", "#", ":"))]
        for l in body:
            if l.startswith(("Subject: ", "Commits of this", "git -C ")):
                continue
            self.assertRegex(l, r"^(Observed|Judged): |^A contributor here|^Dates are", l)
        self.assertNotRegex(self.doc, r"(?i)\b(main author|top contributor|most important|same person as)\b")

    def test_by_date_under_each_path_and_the_dates_are_the_commits(self):
        exp = self.expected()
        by_short = {h[:7]: v for h, v in exp.items()}
        for s in sections(self.doc):
            if s["id"] and s["id"].startswith("person-"):
                last_path = None
                dates = {}
                for l in s["body"]:
                    m = re.match(r"^- \[\[#path-[^\]]*\]\[(.*?)\]\]$", l)
                    if m:
                        last_path = m.group(1).replace(ZW, "")
                        dates[last_path] = []
                    m = re.match(r"^  - (\d{4}-\d\d-\d\d) \[\[#commit-([0-9a-f]+)\]", l)
                    if m:
                        dates[last_path].append(m.group(1))
                        self.assertEqual(by_short[m.group(2)][2], m.group(1))
                for p, ds in dates.items():
                    self.assertEqual(ds, sorted(ds), p)

    def test_odd_paths_names_and_subjects_cannot_become_org_syntax(self):
        ids, edges = graph(self.doc)
        self.assertIn("path-" + enc("weird [[x]].txt"), ids)
        self.assertIn("path-" + enc("\u00fcn\u00ef.txt"), ids)
        self.assertIn("path-" + enc("sp ace.txt"), ids)
        self.assertIn("person-" + enc("dependabot[bot] <bot@example.org>"), ids)
        for (a, b) in edges:
            self.assertIn(b, ids)
        # no heading but the ones we expect, no line that Org would take for a keyword or a block
        heads = [l for l in self.doc.split("\n") if HEAD_RE.match(l)]
        n_ids = len(ids)
        self.assertEqual(len(heads), 4 + n_ids)
        keywords = [l for l in self.doc.split("\n") if l.startswith("#+")]
        self.assertEqual(keywords, ["#+TITLE: Who worked on this, over time", "#+BEGIN_EXAMPLE", "#+END_EXAMPLE"])
        subj = [l for s in sections(self.doc) for l in s["body"] if l.startswith("Subject: ")]
        self.assertTrue(any("evil" in l and "[[evil]]" not in l for l in subj))     # the link opener is broken
        self.assertTrue(any(l.startswith("Subject: * not a heading") for l in subj))
        # a link label read back is the name as git has it
        lab = [l for l in LINK_RE.findall(self.doc) if l[0] == "person-" + enc("dependabot[bot] <bot@example.org>")][0][1]
        self.assertEqual(lab.replace(ZW, ""), "dependabot[bot] <bot@example.org>")

    def test_commits_that_list_no_path_are_still_in_the_triangle_with_their_person(self):
        ids, edges = graph(self.doc)
        empties = [h for h, v in self.expected().items() if not v[4]]
        self.assertEqual(len(empties), 2)   # the empty commit and the merge
        for h in empties:
            cid = "commit-" + [i for i in ids if i.startswith("commit-") and h.startswith(i[7:])][0][7:]
            out = [b for a, b in edges if a == cid]
            self.assertEqual([kind(b) for b in out], ["person"])
            self.assertIn("none listed", "\n".join(ids[cid]["body"]))
            self.assertIn((out[0], cid), set(edges))

    def test_since_until_and_paths_narrow_the_range_and_say_so(self):
        exp = self.expected()
        doc = self.who("--since", "2026-03-01", "--until", "2026-04-02")
        ids, _ = graph(doc)
        want = [h for h, v in exp.items() if "2026-03-01" <= v[2] <= "2026-04-02"]
        self.assertEqual(sum(1 for i in ids if i.startswith("commit-")), len(want))
        self.assertIn("since 2026-03-01 00:00:00, until 2026-04-02 23:59:59", doc)      # a bare date is the whole day
        self.assertIn("--since=2026-03-01 00:00:00 --until=2026-04-02 23:59:59", doc)
        doc = self.who("dir")
        ids, _ = graph(doc)
        self.assertEqual(sum(1 for i in ids if i.startswith("commit-")), 2)
        self.assertIn("limited to the paths given", doc)
        doc = self.who("--since", "2030-01-01")
        self.assertIn("It holds 0 commits by 0 contributors", doc)
        self.assertEqual(graph(doc)[0], {})

    def test_a_repository_without_commits_and_a_path_that_is_not_one(self):
        empty = os.path.join(self.td, "empty")
        os.makedirs(empty)
        git(empty, "init", "-q")
        doc = self.who(repo=empty)
        self.assertIn("no commit at all", doc)
        rc, so, err = orgfs("who", os.path.join(self.td, "nowhere"))
        self.assertNotEqual(rc, 0)
        self.assertIn("not a git repository", err)
        rc, so, err = orgfs("who")
        self.assertNotEqual(rc, 0)
        rc, so, err = orgfs("who", self.repo, "--since", "-1")
        self.assertNotEqual(rc, 0)

    def test_a_shallow_clone_says_so(self):
        sh = os.path.join(self.td, "shallow")
        subprocess.run([GIT, "clone", "-q", "--depth", "1", "file://" + self.repo, sh], capture_output=True, check=True,
                       env=dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1"))
        doc = self.who(repo=sh)
        self.assertIn("This is a shallow clone", doc)
        self.assertNotIn("This is a shallow clone", self.doc)

    def test_the_tool_changes_nothing_in_the_repository(self):
        before = git(self.repo, "status", "--porcelain", "--ignored") + git(self.repo, "rev-parse", "HEAD") + git(self.repo, "count-objects", "-v")
        self.who()
        after = git(self.repo, "status", "--porcelain", "--ignored") + git(self.repo, "rev-parse", "HEAD") + git(self.repo, "count-objects", "-v")
        self.assertEqual(before, after)

    def test_no_network_call_and_nothing_but_log_and_rev_parse(self):
        shim = os.path.join(self.td, "shim")
        os.makedirs(shim, exist_ok=True)
        logf = os.path.join(self.td, "shim.log")
        open(logf, "w").close()
        real_git = GIT
        with open(os.path.join(shim, "git"), "w") as f:
            f.write('#!/bin/sh\necho "git $*" >> %s\nexec %s "$@"\n' % (logf, real_git))
        for name in ("curl", "wget", "ssh", "nc", "ncat", "socat", "telnet", "ftp", "apt-get", "apt", "pip", "pip3"):
            with open(os.path.join(shim, name), "w") as f:
                f.write('#!/bin/sh\necho "%s $*" >> %s\nexit 1\n' % (name, logf))
        for name in os.listdir(shim):
            os.chmod(os.path.join(shim, name), stat.S_IRWXU)
        env = {"PATH": shim + os.pathsep + os.environ["PATH"]}
        rc, so, err = orgfs("who", self.repo, env=env)
        self.assertEqual(rc, 0, err)
        with open(logf) as f:
            calls = f.read().splitlines()
        self.assertTrue(calls)
        for c in calls:
            self.assertRegex(c, r"^git -C \S+ (-c \S+ )*(log|rev-parse) ", c)
        self.assertFalse([c for c in calls if not c.startswith("git ")], calls)
        # and the code of the command names no network tool or verb
        with open(T.BIN) as f:
            src = f.read()
        region = src[src.index("# ------------------------------------------------------------------ who"):src.index("# ------------------------------------------------------------------ stack")]
        code = "\n".join(l for l in region.split("\n") if not l.lstrip().startswith("#"))
        bad = re.search(r"\bgit\b[^\n]*\b(fetch|pull|clone|push|ls-remote|remote|submodule)\b|\b(curl|wget|ssh|rsync|ftp|nc|ncat)\b|https?://", code)
        self.assertIsNone(bad, bad and bad.group(0))

    def test_same_output_in_a_shuffled_environment(self):
        rc, a, _ = orgfs("who", self.repo, env={"LC_ALL": "en_US.UTF-8", "TZ": "Asia/Tokyo"})
        rc2, b, _ = orgfs("who", self.repo, env={"LC_ALL": "C", "TZ": "America/Chicago"})
        strip = lambda t: re.sub(r"20\d\d-\d\d-\d\dT\d\d:\d\d:\d\dZ", "NOW", t)
        self.assertEqual(strip(a), strip(b))

    def test_out_writes_a_file_and_prints_nothing(self):
        out = os.path.join(self.td, "w.org")
        rc, so, err = orgfs("who", self.repo, "--out", out)
        self.assertEqual((rc, so), (0, ""))
        with open(out, encoding="utf-8") as f:
            self.assertEqual(re.sub(r"20\d\d-\d\d-\d\dT\d\d:\d\d:\d\dZ", "NOW", f.read()), re.sub(r"20\d\d-\d\d-\d\dT\d\d:\d\d:\d\dZ", "NOW", self.doc))


class WhoScaleTests(unittest.TestCase):
    def test_a_few_hundred_commits_and_paths(self):
        if GIT is None:
            self.skipTest("NOT CHECKED: no git here")
        with tempfile.TemporaryDirectory() as td:
            repo = os.path.join(td, "r")
            os.makedirs(repo)
            git(repo, "init", "-q", "-b", "main")
            lines = []
            n = 300
            for i in range(n):
                who = "P%d" % (i % 7)
                body = "c%d\n" % i
                msg = "commit %d" % i
                lines.append("commit refs/heads/main\ncommitter %s <%s@example.org> %d +0000\ndata %d\n%s\n" % (who, who, 1700000000 + i * 3600, len(msg), msg))
                for k in range(3):
                    p = "d%d/f%d.txt" % ((i + k) % 11, (i * 3 + k) % 60)
                    lines.append("M 100644 inline %s\ndata %d\n%s" % (p, len(body), body))
                lines.append("\n")
            data = "".join(lines).replace("committer", "author P <p@x> 1 +0000\ncommitter", 0)
            subprocess.run([GIT, "-C", repo, "fast-import", "--quiet"], input=data.encode(), check=True, capture_output=True)
            rc, so, err = orgfs("who", repo)
            self.assertEqual(rc, 0, err)
            ids, edges = graph(so)
            self.assertEqual(sum(1 for i in ids if i.startswith("commit-")), n)
            self.assertEqual(sum(1 for i in ids if i.startswith("person-")), 7)
            eset = set(edges)
            self.assertTrue(all((b, a) in eset for a, b in edges if kind(a) != kind(b)))


class WhoSiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if GIT is None:
            raise unittest.SkipTest("NOT CHECKED: no git here")
        cls._td = tempfile.TemporaryDirectory()
        cls.td = cls._td.name
        cls.repo = make_repo(cls.td)
        cls.out = os.path.join(cls.td, "site")
        rc, so, err = orgfs("site", os.path.join(T.ROOT_DIR, "examples", "licensing.d"), cls.out, "--repo", cls.repo)
        assert rc == 0, err
        with open(os.path.join(cls.out, "who.html"), encoding="utf-8") as f:
            cls.html = f.read()

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_who_html_is_in_the_nav_of_every_page_and_marked_current_on_its_own(self):
        for n in ("index.html", "made.html", "gaps.html", "licensing.html", "rights.html", "who.html"):
            with open(os.path.join(self.out, n), encoding="utf-8") as f:
                html = f.read()
            nav = re.search(r"<nav.*?</nav>", html, re.S).group(0)
            self.assertIn('href="who.html"', nav, n)
            self.assertEqual(re.findall(r"<a [^>]*>(.*?)</a>", nav).count("Who worked on this"), 1, n)
        nav = re.search(r"<nav.*?</nav>", self.html, re.S).group(0)
        self.assertEqual(re.findall(r'<a href="([^"]+)" aria-current="page"', nav), ["who.html"])
        self.assertIn("<title>Who worked on this, over time - orgfs</title>", self.html)

    def test_the_page_is_the_document_every_href_has_an_id_and_the_forms_hold(self):
        hrefs = set(re.findall(r'href="#([^"]+)"', self.html))
        idset = set(re.findall(r'\bid="([^"]+)"', self.html))
        self.assertTrue(hrefs)
        self.assertEqual(hrefs - idset, set())
        with open(os.path.join(self.out, "who.org"), encoding="utf-8") as f:
            org = f.read()
        ids, _ = graph(org)
        self.assertEqual(set(i for i in idset if i.startswith(("person-", "path-", "commit-"))), set(ids))
        self.assertEqual(self.html.count('class="status"'), 1)
        self.assertLess(self.html.index('class="status"'), self.html.index("<h2>"))
        self.assertIn("<h2>Not checked</h2>", self.html)
        self.assertIn('<span class="tag">observed</span>', self.html)
        # names and emails as the commits give them, escaped for HTML
        self.assertIn("Alice Example &lt;alice@example.org&gt;", self.html)
        self.assertIn("alice example &lt;alice@example.org&gt;", self.html)
        self.assertIn("BOB@EXAMPLE.ORG", self.html)
        self.assertNotIn("<alice@", self.html)
        self.assertIn('<a href="who.org">who.org</a>', self.html)
        # nothing from another host, no script, no style attribute
        self.assertNotRegex(self.html, r"<script|style=|src=\"http|href=\"http://")
        self.assertEqual(set(re.findall(r'href="(https?://[^"]+)"', self.html)), {"https://github.com/inputloop/orgfs"})
        self.assertNotIn(self.repo, self.html)         # the path of the build host is not published
        with open(os.path.join(self.out, "who.org"), encoding="utf-8") as f:
            self.assertNotIn(self.repo, f.read())

    def test_the_site_built_inside_a_checkout_reads_that_checkout_by_default(self):
        co = os.path.join(self.td, "checkout")
        os.makedirs(os.path.join(co, "bin"))
        shutil.copy(T.BIN, os.path.join(co, "bin", "orgfs"))
        shutil.copytree(os.path.join(T.ROOT_DIR, "share"), os.path.join(co, "share"))
        git(co, "init", "-q", "-b", "main")
        commit(co, "Dee Example", "dee@example.org", "2026-05-01T00:00:00+00:00", "the tool and its register", {"bin/orgfs.note": "x\n"})
        out = os.path.join(self.td, "site2")
        p = subprocess.run(["sh", os.path.join(co, "bin", "orgfs"), "site", os.path.join(T.ROOT_DIR, "examples", "licensing.d"), out], capture_output=True, text=True, cwd=self.td)
        self.assertEqual(p.returncode, 0, p.stderr)
        with open(os.path.join(out, "who.html"), encoding="utf-8") as f:
            html = f.read()
        self.assertIn("Dee Example &lt;dee@example.org&gt;", html)
        self.assertNotIn("Alice Example", html)

    def test_a_copy_outside_any_repository_says_there_was_no_history(self):
        loose = os.path.join(self.td, "loose")
        os.makedirs(os.path.join(loose, "bin"))
        shutil.copy(T.BIN, os.path.join(loose, "bin", "orgfs"))
        shutil.copytree(os.path.join(T.ROOT_DIR, "share"), os.path.join(loose, "share"))
        out = os.path.join(self.td, "site3")
        p = subprocess.run(["sh", os.path.join(loose, "bin", "orgfs"), "site", os.path.join(T.ROOT_DIR, "examples", "licensing.d"), out],
                           capture_output=True, text=True, cwd=self.td, env=dict(os.environ, GIT_CEILING_DIRECTORIES=self.td))
        self.assertEqual(p.returncode, 0, p.stderr)
        with open(os.path.join(out, "who.html"), encoding="utf-8") as f:
            html = f.read()
        self.assertIn("no history to read", html)
        self.assertIn("NOT CHECKED", html)
        self.assertFalse(os.path.exists(os.path.join(out, "who.org")))


if __name__ == "__main__":
    unittest.main()
