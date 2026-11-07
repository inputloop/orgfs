#!/usr/bin/env python3
# Copyright 2026 Corwin Brust <corwin@bru.st> and Contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for several spokes per file, views (materialize), collections (assemble),
gap records, back-links and routes.  Python 3 standard library only.

The expectations here are worked out from the spec in README.org, not read back
from the tool: which gap records a given host must be shown, which strings must
never appear in its view, which lines of the declaration a gap may cite.
"""
import hashlib
import os
import random
import re
import stat
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_orgfs as T  # noqa: E402

orgfs = T.orgfs
sha = T.sha

SPOKES = {
    "pub":    dict(ring="GREAT", stripes=3, replicas=2, vis="public",  members=["h1", "h2", "h3", "h4"]),
    "priv":   dict(ring="GRAIN", stripes=3, replicas=2, vis="private", members=["h2", "h3", "h4"]),
    "sealed": dict(ring="GRAIN", stripes=2, replicas=1, vis="sealed",  members=["h5", "h6"]),
}
VIEWERS = ["out", "h1", "h2", "h5"]   # a non-member, a pub member, a pub+priv member, a sealed member


def parse_org(text):
    recs, cur, indr = [], None, False
    for n, line in enumerate(text.split("\n"), 1):
        m = re.match(r"^(\*+) (.*)$", line)
        if m:
            cur = {"level": len(m.group(1)), "title": m.group(2), "props": [], "line": n}
            recs.append(cur)
            indr = False
            continue
        if cur is None:
            continue
        if line.strip() == ":PROPERTIES:":
            indr = True
        elif line.strip() == ":END:":
            indr = False
        elif indr:
            mm = re.match(r"^:([A-Z0-9_]+): ?(.*)$", line)
            if mm:
                cur["props"].append((mm.group(1), mm.group(2)))
    return recs


def slurp(p):
    with open(p) as f:
        return f.read()


def props(rec, k):
    return [v for kk, v in rec["props"] if kk == k]


def prop(rec, k):
    v = props(rec, k)
    return v[0] if v else None


def gaps_of(text):
    return [r for r in parse_org(text) if r["title"].startswith("gap ")]


def gap_ids(text):
    return {prop(g, "GAP_ID") for g in gaps_of(text)}


def ref_spoke_identity(name, sp, roots):
    rows = ["spoke\tname\t%s" % name, "spoke\tRING\t%s" % sp["ring"], "spoke\tSTRIPES\t%d" % sp["stripes"],
            "spoke\tREPLICAS\t%d" % sp["replicas"], "spoke\tVISIBILITY\t%s" % sp["vis"]]
    for h in sp["members"]:
        rows += ["member\t%s\tROOT\t%s" % (h, roots[h]), "member\t%s\tTRANSPORT\tlocal" % h]
    return sha(("orgfs-decl-1\n" + "".join(r + "\n" for r in sorted(rows))).encode())


def ref_file_identity(ids):
    return sha(("orgfs-file-1\n" + "".join(i + "\n" for i in sorted(ids))).encode())


def shown(text, s):
    """True if a withheld string appears in text. A member name (a short token such as h6) counts only
    as a whole name, so a random temporary folder like tmph6ai_fh1 is not read as a leak; a path or a
    hash counts anywhere."""
    if re.fullmatch(r"[A-Za-z0-9_-]{1,32}", s):
        return re.search(r"(?<![A-Za-z0-9_.-])" + re.escape(s) + r"(?![A-Za-z0-9_.-])", text) is not None
    return s in text


class ShownTests(unittest.TestCase):
    """The leak check itself: a name inside a random folder is not a leak; a whole name is."""

    def test_a_name_inside_a_random_folder_is_not_a_leak(self):
        self.assertFalse(shown(":BYTES_AT: /tmp/tmpf4gv4ffo/tmph6ai_fh1/pub/x", "h6"))

    def test_a_whole_name_is_a_leak(self):
        for t in ("** member h6", ":REACH: h5 h6", ":ROOT: /spokes/sealed/h6", "h6"):
            self.assertTrue(shown(t, "h6"), t)

    def test_a_path_counts_anywhere(self):
        self.assertTrue(shown("x/tmp/a/sealed/h6/y", "/tmp/a/sealed/h6"))


class World:
    """Three spokes over local directories: pub (public), priv (private), sealed."""

    def __init__(self, d, order=None, prose=""):
        self.d = d
        self.roots = {}
        for s, sp in SPOKES.items():
            for h in sp["members"]:
                r = os.path.join(d, s, h)
                os.makedirs(r)
                self.roots[(s, h)] = r
        os.makedirs(os.path.join(d, "_in"))
        self.decl = os.path.join(d, "w.org")
        self.objects = {s: {} for s in SPOKES}
        self.write(order or sorted(SPOKES), prose)
        self.n = 0

    def write(self, order, prose=""):
        lines = ["#+TITLE: test world", "", prose, ""]
        for s in order:
            sp = SPOKES[s]
            lines += ["* spoke %s" % s, ":PROPERTIES:", ":RING: %s" % sp["ring"], ":STRIPES: %d" % sp["stripes"],
                      ":REPLICAS: %d" % sp["replicas"], ":VISIBILITY: %s" % sp["vis"], ":END:", ""]
            for h in sp["members"]:
                lines += ["** member %s" % h, ":PROPERTIES:", ":ROOT: %s" % self.roots[(s, h)],
                          ":TRANSPORT: local", ":END:", ""]
        with open(self.decl, "w") as f:
            f.write("\n".join(lines) + "\n")

    def put(self, spoke, data):
        self.n += 1
        p = os.path.join(self.d, "_in", "f%d" % self.n)
        with open(p, "wb") as f:
            f.write(data)
        rc, out, err = orgfs("put", self.decl, p, "--spoke", spoke)
        assert rc == 0, err
        oid = out.strip()
        assert oid == sha(data)
        self.objects[spoke][oid] = data
        return oid

    def fill(self):
        self.A = self.put("pub", os.urandom(3001))
        self.B = self.put("pub", b"tiny")
        self.C = self.put("priv", os.urandom(700))

    def lines(self):
        with open(self.decl) as f:
            return f.read().split("\n")

    def expect(self, viewer, verb="materialize", spoke=None, obj=None):
        ids = set()
        for s in sorted(SPOKES):
            sp = SPOKES[s]
            if spoke and spoke != s:
                ids.add("%s/grain" % s)
                continue
            member = viewer in sp["members"]
            if sp["vis"] == "sealed" and not member:
                ids.add("%s/sealed" % s)
                continue
            acc = "member" if member else ("read" if sp["vis"] == "public" else "none")
            if acc == "none":
                ids |= {"%s/member-root/%s" % (s, h) for h in sp["members"]}
                ids.add("%s/objects" % s)
                continue
            for o in self.objects[s]:
                if obj and o != obj:
                    ids.add("%s/object-grain/%s" % (s, o))
                elif verb == "materialize":
                    n = sp["stripes"]
                    ids |= {"%s/slice-bytes/%s/%d.of.%d" % (s, o, i, n) for i in range(1, n + 1)}
        return ids

    def withheld_strings(self, viewer):
        """Content a viewer must never be shown."""
        out = []
        for s, sp in SPOKES.items():
            member = viewer in sp["members"]
            if sp["vis"] == "sealed" and not member:
                out += [self.roots[(s, h)] for h in sp["members"]] + list(sp["members"])
                rc, o, _ = orgfs("check", self.decl, "--spoke", s)
                out.append(re.search(r"identity\t([0-9a-f]{64})", o).group(1))
            elif sp["vis"] == "private" and not member:
                out += [self.roots[(s, h)] for h in sp["members"]]
                for oid in self.objects[s]:
                    out.append(oid)
        return out


def check_gap_records(tc, world, text):
    """Every gap record is complete and its declaring lines are real lines."""
    decl = world.lines()
    for g in gaps_of(text):
        gid = prop(g, "GAP_ID")
        for k in ("WHAT_KIND", "WHAT_SHAPE", "RULE", "RULE_AT", "HOW", "WHY", "REACH"):
            tc.assertTrue(prop(g, k), (gid, k))
        tc.assertTrue(props(g, "ROUTE"), gid)
        tc.assertEqual(g["title"], "gap " + gid)
        at = prop(g, "RULE_AT")
        tc.assertRegex(at, r"^(line \d+ \(:[A-Z]+:\)|not in the declaration: )", (gid, at))
        for n, key in re.findall(r"line (\d+) \(:([A-Z]+):\)", at):
            tc.assertTrue(decl[int(n) - 1].strip().upper().startswith(":%s:" % key), (gid, at, decl[int(n) - 1]))


class ViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.w = World(cls._td.name)
        cls.w.fill()
        cls.views = {}
        for v in VIEWERS:
            rc, out, err = orgfs("materialize", cls.w.decl, "--as", v)
            assert rc == 0, err
            cls.views[v] = out

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_gap_records_are_exactly_the_expected_ones(self):
        for v in VIEWERS:
            self.assertEqual(gap_ids(self.views[v]), self.w.expect(v), v)

    def test_every_gap_is_complete_and_cites_a_real_line(self):
        for v in VIEWERS:
            check_gap_records(self, self.w, self.views[v])

    def test_nothing_withheld_is_in_the_view(self):
        for v in VIEWERS:
            for s in self.w.withheld_strings(v):
                self.assertFalse(shown(self.views[v], s), (v, s))

    def test_members_see_what_outsiders_do_not(self):
        self.assertIn(self.w.roots[("priv", "h2")], self.views["h2"])
        self.assertIn(self.w.C, self.views["h2"])
        self.assertIn(self.w.roots[("sealed", "h5")], self.views["h5"])
        self.assertNotIn(self.w.roots[("priv", "h2")], self.views["h1"])
        self.assertNotIn(self.w.C, self.views["out"])

    def test_names_of_withheld_things_are_shown(self):
        out = self.views["out"]
        self.assertIn("gap sealed/sealed", out)
        for h in SPOKES["priv"]["members"]:
            self.assertIn("gap priv/member-root/%s" % h, out)
        spokes = [r["title"] for r in parse_org(out) if r["level"] == 1 and r["title"].startswith("spoke ")]
        self.assertEqual(spokes, ["spoke priv", "spoke pub"])

    def test_every_fetched_item_names_its_source_and_the_source_exists(self):
        decl_roots = {os.path.realpath(r) for r in self.w.roots.values()}
        n_items = 0
        for v in ("out", "h1", "h2"):
            for r in parse_org(self.views[v]):
                if prop(r, "ROLE") != "fetched":
                    continue
                n_items += 1
                for k in ("SOURCE_SPOKE", "SOURCE_OBJECT", "SOURCE_MEMBER", "SOURCE_PATH"):
                    self.assertTrue(prop(r, k), (v, r["title"], k))
                path = prop(r, "SOURCE_PATH")
                self.assertTrue(os.path.exists(path), path)
                sp, h = prop(r, "SOURCE_SPOKE"), prop(r, "SOURCE_MEMBER")
                self.assertIn(h, SPOKES[sp]["members"])
                self.assertTrue(path.startswith(self.w.roots[(sp, h)] + "/.orgfs/%s/%s/" % (sp, prop(r, "SOURCE_OBJECT"))), path)
                if r["title"].startswith("slice "):
                    self.assertEqual(prop(r, "SOURCE_SLICE"), r["title"][len("slice "):])
                    i = int(prop(r, "SOURCE_SLICE").split(".")[0])
                    with open(path) as f:
                        row = f.read().split("\n")[i - 1].split("\t")
                    self.assertEqual((row[1], row[2]), (prop(r, "SIZE"), prop(r, "SHA256")))
        self.assertGreater(n_items, 10)

    def test_slice_items_agree_with_the_independent_plan(self):
        for r in parse_org(self.views["out"]):
            if r["title"].startswith("slice ") and prop(r, "SOURCE_SPOKE") == "pub":
                i = int(prop(r, "SOURCE_SLICE").split(".")[0])
                want = T.ref_plan("pub", SPOKES["pub"]["members"], 3, 2)[i]
                self.assertEqual(sorted(prop(r, "HOLDERS").split()), want)
                self.assertEqual(prop(r, "HELD_HERE"), "no")
        for r in parse_org(self.views["h1"]):
            if r["title"].startswith("slice ") and prop(r, "SOURCE_SPOKE") == "pub":
                i = int(prop(r, "SOURCE_SLICE").split(".")[0])
                held = "h1" in T.ref_plan("pub", SPOKES["pub"]["members"], 3, 2)[i]
                self.assertEqual(prop(r, "HELD_HERE"), "yes" if held else "no")

    def test_slice_gap_rule_depends_on_holding(self):
        for g in gaps_of(self.views["h1"]):
            if prop(g, "WHAT_KIND") != "slice-bytes":
                continue
            i = int(prop(g, "GAP_ID").rsplit("/", 1)[1].split(".")[0])
            held = "h1" in T.ref_plan("pub", SPOKES["pub"]["members"], 3, 2)[i]
            self.assertEqual(prop(g, "RULE_AT").startswith("not in the declaration"), held, prop(g, "GAP_ID"))

    def test_member_root_gap_names_the_member_not_the_path(self):
        g = [g for g in gaps_of(self.views["out"]) if prop(g, "GAP_ID") == "priv/member-root/h3"][0]
        self.assertEqual(prop(g, "WHAT_KIND"), "member-root")
        self.assertEqual(prop(g, "WHAT_SHAPE"), "1 absolute path, %d components" % (self.w.roots[("priv", "h3")].count("/")))
        self.assertEqual(sorted(prop(g, "REACH").split()), sorted(SPOKES["priv"]["members"]))
        self.assertIn("VISIBILITY", prop(g, "RULE"))
        self.assertRegex(prop(g, "RULE_AT"), r"^line \d+ \(:VISIBILITY:\); withheld field declared at line \d+ \(:ROOT:\)$")

    def test_sealed_gap_hides_member_names_even_in_reach_and_routes(self):
        g = [g for g in gaps_of(self.views["out"]) if prop(g, "GAP_ID") == "sealed/sealed"][0]
        blob = "\n".join(v for _, v in g["props"])
        self.assertNotIn("h5", blob)
        self.assertNotIn("h6", blob)
        self.assertIn("names withheld", prop(g, "REACH"))
        g5 = [g for g in gaps_of(self.views["h5"]) if prop(g, "GAP_ID").startswith("sealed/")]
        self.assertEqual(g5, [])

    def test_view_is_deterministic(self):
        for v in ("out", "h2"):
            self.assertEqual(orgfs("materialize", self.w.decl, "--as", v)[1], self.views[v])

    def test_views_differ_by_host_and_carry_a_content_bound_id(self):
        ids = {v: self.views[v].strip().split("\n")[-1] for v in VIEWERS}
        for v, last in ids.items():
            m = re.match(r"^# view-id: ([0-9a-f]{64})$", last)
            self.assertTrue(m, last)
            body = self.views[v][: self.views[v].rindex("# view-id:")]
            self.assertEqual(m.group(1), sha(body.encode()))
        self.assertEqual(len(set(ids.values())), len(VIEWERS))

    def test_file_id_in_view_matches_the_independent_identity(self):
        ids = [ref_spoke_identity(s, sp, {h: self.w.roots[(s, h)] for h in sp["members"]}) for s, sp in SPOKES.items()]
        want = ref_file_identity(ids)
        for v in VIEWERS:
            self.assertEqual(re.search(r"^:FILE_ID: (\w+)$", self.views[v], re.M).group(1), want)

    def test_backlinks_exist_and_name_the_view_and_host(self):
        for v in ("out", "h2"):
            vid = re.search(r"# view-id: (\w+)\s*$", self.views[v]).group(1)
            fid = re.search(r"^:FILE_ID: (\w+)$", self.views[v], re.M).group(1)
            n = 0
            for r in parse_org(self.views[v]):
                for bl in props(r, "BACKLINK"):
                    n += 1
                    with open(bl) as f:
                        rows = [l.split("\t") for l in f.read().split("\n") if l]
                    mine = [x for x in rows if x[0] == vid]
                    self.assertTrue(mine, bl)
                    self.assertEqual(mine[0][1], v)
                    self.assertEqual(mine[0][2], "materialized")
                    self.assertEqual(mine[0][3], fid)
                    self.assertEqual(mine[0][4], prop(r, "SOURCE_SPOKE"))
                    self.assertEqual(mine[0][5], prop(r, "SOURCE_OBJECT"))
            self.assertGreater(n, 0)

    def test_materialize_twice_leaves_one_record(self):
        vid = re.search(r"# view-id: (\w+)\s*$", self.views["h1"]).group(1)
        orgfs("materialize", self.w.decl, "--as", "h1")
        orgfs("materialize", self.w.decl, "--as", "h1")
        for r in parse_org(self.views["h1"]):
            for bl in props(r, "BACKLINK"):
                with open(bl) as f:
                    self.assertEqual(sum(1 for l in f if l.startswith(vid)), 1, bl)

    def test_trace_follows_a_view_to_its_sources_and_back(self):
        out = os.path.join(self._td.name, "h2.org")
        rc, so, err = orgfs("materialize", self.w.decl, "--as", "h2", "--out", out)
        self.assertEqual(rc, 0, err)
        self.assertIn("wrote", so)
        rc, so, err = orgfs("trace", out)
        self.assertEqual(rc, 0, so + err)
        self.assertIn("trace: OK", so)
        self.assertIn("linked", so)
        vid = re.search(r"# view-id: (\w+)\s*$", slurp(out)).group(1)
        rc, so, err = orgfs("trace", self.w.decl, "--spoke", "priv", "--object", self.w.C)
        self.assertEqual(rc, 0, err)
        self.assertIn(vid, so)
        self.assertIn("as h2", so)

    def test_trace_refuses_an_edited_view(self):
        out = os.path.join(self._td.name, "edited.org")
        orgfs("materialize", self.w.decl, "--as", "h1", "--out", out)
        with open(out) as f:
            text = f.read()
        with open(out, "w") as f:
            f.write(text.replace(":AS: h1", ":AS: h2", 1))
        rc, so, _ = orgfs("trace", out)
        self.assertNotEqual(rc, 0)
        self.assertIn("BROKEN", so)

    def test_trace_object_with_no_views(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        w = World(d.name)
        o = w.put("pub", b"nobody has looked at me")
        rc, so, err = orgfs("trace", w.decl, "--spoke", "pub", "--object", o)
        self.assertEqual(rc, 0, err)
        self.assertIn("0 record", so)

    # ------------------------------------------------------------ routes
    def test_routes_lists_every_gap_with_its_routes(self):
        out = os.path.join(self._td.name, "out.org")
        orgfs("materialize", self.w.decl, "--as", "out", "--out", out)
        rc, so, err = orgfs("routes", out)
        self.assertEqual(rc, 0, err)
        gs = gaps_of(slurp(out))
        listed = re.findall(r"^gap (\S+) \(kind", so, re.M)
        self.assertEqual(sorted(listed), sorted(prop(g, "GAP_ID") for g in gs))
        for g in gs:
            for r in props(g, "ROUTE"):
                self.assertIn("fill by: " + r, so)
        self.assertIn("routes: %d gaps" % len(gs), so)

    def test_routes_refuses_an_incomplete_gap(self):
        out = os.path.join(self._td.name, "bad.org")
        text = self.views["out"]
        bad = re.sub(r"^:RULE_AT: .*\n", "", text, count=1, flags=re.M)
        with open(out, "w") as f:
            f.write(bad)
        rc, so, err = orgfs("routes", out)
        self.assertNotEqual(rc, 0)
        self.assertIn("RULE_AT", err)
        lines, seen, kept = text.split("\n"), False, []
        for ln in lines:                      # take every route out of the first gap record
            if ln.startswith(":ROUTE: ") and not seen:
                continue
            if ln.startswith(":END:") and any(l.startswith(":ROLE: gap") for l in kept[-14:]):
                seen = True
            kept.append(ln)
        with open(out, "w") as f:
            f.write("\n".join(kept))
        rc, so, err = orgfs("routes", out)
        self.assertNotEqual(rc, 0)
        self.assertIn("no route", err)

    def test_following_every_route_fills_its_gap(self):
        """Reassembling from the gap records alone: each route, run, yields what the gap stood for."""
        decl = self.w.decl
        seen = set()
        for g in gaps_of(self.views["out"]):
            kind, gid = prop(g, "WHAT_KIND"), prop(g, "GAP_ID")
            for route in props(g, "ROUTE"):
                m = re.match(r"^on [^:]+: (orgfs .*)$", route)
                self.assertTrue(m, route)
                if (m.group(1), kind) in seen:
                    continue
                seen.add((m.group(1), kind))
                into = tempfile.mkdtemp(dir=self._td.name)
                argv = [{"DECL": decl, "DIR": into, "MEMBER": "h5", "THAT_HOST": "elsewhere"}.get(t, t)
                        for t in m.group(1).split()[1:]]
                rc, so, err = orgfs(*argv)
                self.assertEqual(rc, 0, (route, err))
                if kind == "slice-bytes":
                    s, o = gid.split("/")[0], gid.split("/")[2]
                    with open(os.path.join(into, s, o), "rb") as f:
                        self.assertEqual(sha(f.read()), o)
                elif kind == "objects":
                    s = gid.split("/")[0]
                    for o, data in self.w.objects[s].items():
                        with open(os.path.join(into, s, o), "rb") as f:
                            self.assertEqual(f.read(), data)
                elif kind == "member-root":
                    s, h = gid.split("/")[0], gid.split("/")[2]
                    self.assertIn(":ROOT: " + self.w.roots[(s, h)], so)
                elif kind == "spoke":
                    s = gid.split("/")[0]
                    self.assertIn("* spoke " + s + "\n", so)
                    self.assertIn(":ACCESS: ", so)
        kinds = {k for _, k in seen}
        self.assertEqual(kinds, {"slice-bytes", "objects", "member-root", "spoke"})


class AssembleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.w = World(cls._td.name)
        cls.w.fill()

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def into(self):
        return tempfile.mkdtemp(dir=self._td.name)

    def assemble(self, viewer, *extra):
        into = self.into()
        rc, out, err = orgfs("assemble", self.w.decl, "--as", viewer, "--into", into, *extra)
        self.assertEqual(rc, 0, err)
        return into, out

    def files(self, into):
        found = {}
        for s in os.listdir(into):
            for o in os.listdir(os.path.join(into, s)):
                with open(os.path.join(into, s, o), "rb") as f:
                    found[(s, o)] = f.read()
        return found

    def test_all_spokes_gathers_what_the_host_may_reach(self):
        for v, want in (("out", {"pub"}), ("h1", {"pub"}), ("h2", {"pub", "priv"}), ("h5", {"pub"})):
            into, out = self.assemble(v)
            got = self.files(into)
            self.assertEqual({s for s, _ in got}, want, v)
            for (s, o), data in got.items():
                self.assertEqual(data, self.w.objects[s][o])
            self.assertEqual(gap_ids(out), self.w.expect(v, "assemble"), v)
            check_gap_records(self, self.w, out)

    def test_nothing_withheld_is_gathered_or_shown(self):
        for v in VIEWERS:
            into, out = self.assemble(v)
            for s in self.w.withheld_strings(v):
                self.assertFalse(shown(out, s), (v, s))
            blob = b"".join(self.files(into).values())
            for s, sp in SPOKES.items():
                if v not in sp["members"] and sp["vis"] != "public":
                    for data in self.w.objects[s].values():
                        self.assertNotIn(data, blob)
                    self.assertFalse(os.path.exists(os.path.join(into, s)), (v, s))

    def test_grain_one_spoke(self):
        into, out = self.assemble("h2", "--spoke", "priv")
        self.assertEqual(set(self.files(into)), {("priv", self.w.C)})
        self.assertEqual(gap_ids(out), self.w.expect("h2", "assemble", spoke="priv"))
        self.assertIn("pub/grain", gap_ids(out))
        self.assertIn("sealed/grain", gap_ids(out))

    def test_grain_one_object(self):
        into, out = self.assemble("out", "--spoke", "pub", "--object", self.w.A)
        self.assertEqual(set(self.files(into)), {("pub", self.w.A)})
        self.assertEqual(gap_ids(out), self.w.expect("out", "assemble", spoke="pub", obj=self.w.A))
        self.assertIn("pub/object-grain/" + self.w.B, gap_ids(out))
        self.assertIn(":GRAIN: object %s in spoke pub" % self.w.A, out)

    def test_object_needs_a_spoke_when_there_are_several(self):
        into = self.into()
        rc, _, err = orgfs("assemble", self.w.decl, "--as", "h1", "--into", into, "--object", self.w.A)
        self.assertNotEqual(rc, 0)
        self.assertIn("--spoke", err)
        rc, _, err = orgfs("assemble", self.w.decl, "--as", "h1", "--into", into, "--spoke", "pub", "--object", "0" * 64)
        self.assertNotEqual(rc, 0)
        self.assertIn("not stored", err)

    def test_assembled_items_name_the_member_the_bytes_came_from(self):
        into, out = self.assemble("h1", "--spoke", "pub")
        for r in parse_org(out):
            if r["title"].startswith("slice ") :
                self.assertEqual(r["props"][0], ("ROLE", "fetched"))
                h, path = prop(r, "SOURCE_MEMBER"), prop(r, "SOURCE_PATH")
                self.assertTrue(path.startswith(self.w.roots[("pub", h)]))
                self.assertTrue(path.endswith(prop(r, "SOURCE_SLICE")), path)
                with open(path, "rb") as f:
                    self.assertEqual(sha(f.read()), prop(r, "SHA256"))
        self.assertNotIn("slice-bytes", out)

    def test_assemble_needs_into_and_materialize_refuses_it(self):
        self.assertNotEqual(orgfs("assemble", self.w.decl, "--as", "h1")[0], 0)
        self.assertNotEqual(orgfs("materialize", self.w.decl, "--as", "h1", "--into", self.into())[0], 0)
        self.assertNotEqual(orgfs("materialize", self.w.decl)[0], 0)
        self.assertNotEqual(orgfs("materialize", self.w.decl, "--as", "bad host")[0], 0)

    def test_assemble_view_is_traceable(self):
        out = os.path.join(self._td.name, "asm.org")
        into = self.into()
        rc, _, err = orgfs("assemble", self.w.decl, "--as", "h2", "--into", into, "--out", out)
        self.assertEqual(rc, 0, err)
        rc, so, err = orgfs("trace", out)
        self.assertEqual(rc, 0, so + err)
        rc, so, _ = orgfs("trace", self.w.decl, "--spoke", "priv", "--object", self.w.C)
        self.assertIn("assembled", so)


class MultiSpokeTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.w = World(self._td.name)

    def ids(self, decl):
        rc, out, err = orgfs("check", decl)
        self.assertEqual(rc, 0, err)
        return out

    def test_check_prints_each_spoke_and_the_file_identity(self):
        out = self.ids(self.w.decl)
        self.assertEqual(re.findall(r"(?m)^spoke\t(\w+)$", out), ["priv", "pub", "sealed"])
        got = re.findall(r"(?m)^identity\t([0-9a-f]{64})$", out)
        want = [ref_spoke_identity(s, SPOKES[s], {h: self.w.roots[(s, h)] for h in SPOKES[s]["members"]}) for s in ("priv", "pub", "sealed")]
        self.assertEqual(got, want)
        self.assertEqual(re.search(r"(?m)^file-identity\t(\w+)$", out).group(1), ref_file_identity(want))

    def test_0_1_0_identity_of_a_single_spoke_is_unchanged(self):
        out = orgfs("check", os.path.join(T.ROOT_DIR, "examples", "licensing.d"), "--no-mounts")[1]
        self.assertIn("identity\t1df8d78c7e2b291b459ee275c505504e655e75ee2e1693eeabe98e23c319e013", out)

    def test_file_identity_ignores_spoke_order_prose_and_member_order(self):
        base = self.ids(self.w.decl)
        rnd = random.Random(2026)
        for k in range(4):
            order = sorted(SPOKES)
            rnd.shuffle(order)
            p = os.path.join(self._td.name, "o%d.org" % k)
            self.w.decl, keep = p, self.w.decl
            self.w.write(order, prose="Different prose %d.\n* An unrelated heading\nwords" % k)
            self.w.decl = keep
            self.assertEqual(self.ids(p), base, order)
            self.assertEqual(orgfs("agree", keep, p)[0], 0)

    def test_file_identity_changes_when_any_one_spoke_changes_and_others_keep_theirs(self):
        base = self.ids(self.w.decl)
        before = dict(zip(("priv", "pub", "sealed"), re.findall(r"(?m)^identity\t(\w+)$", base)))
        SPOKES["pub"]["stripes"] = 4
        try:
            p = os.path.join(self._td.name, "c.org")
            keep, self.w.decl = self.w.decl, p
            self.w.write(sorted(SPOKES))
            self.w.decl = keep
            out = self.ids(p)
        finally:
            SPOKES["pub"]["stripes"] = 3
        after = dict(zip(("priv", "pub", "sealed"), re.findall(r"(?m)^identity\t(\w+)$", out)))
        self.assertNotEqual(after["pub"], before["pub"])
        self.assertEqual(after["priv"], before["priv"])
        self.assertEqual(after["sealed"], before["sealed"])
        self.assertNotEqual(re.search(r"file-identity\t(\w+)", out).group(1), re.search(r"file-identity\t(\w+)", base).group(1))

    def test_agree_per_spoke_ignores_the_other_spokes(self):
        SPOKES["priv"]["stripes"] = 5
        try:
            p = os.path.join(self._td.name, "c.org")
            keep, self.w.decl = self.w.decl, p
            self.w.write(sorted(SPOKES))
            self.w.decl = keep
        finally:
            SPOKES["priv"]["stripes"] = 3
        self.assertNotEqual(orgfs("agree", self.w.decl, p)[0], 0)
        self.assertEqual(orgfs("agree", self.w.decl, p, "--spoke", "pub")[0], 0)
        self.assertNotEqual(orgfs("agree", self.w.decl, p, "--spoke", "priv")[0], 0)

    def test_commands_that_need_one_spoke_say_so(self):
        data = os.path.join(self._td.name, "x")
        with open(data, "wb") as fh:
            fh.write(b"abc")
        for args in (("plan", self.w.decl), ("put", self.w.decl, data), ("verify", self.w.decl),
                     ("get", self.w.decl, "0" * 64, os.path.join(self._td.name, "o"))):
            rc, _, err = orgfs(*args)
            self.assertNotEqual(rc, 0, args)
            self.assertIn("--spoke", err)
        rc, _, err = orgfs("plan", self.w.decl, "--spoke", "nope")
        self.assertNotEqual(rc, 0)
        self.assertIn("no spoke nope", err)
        self.assertEqual(orgfs("plan", self.w.decl, "--spoke", "pub")[0], 0)

    def test_spokes_do_not_see_each_others_objects(self):
        a = self.w.put("pub", b"for pub")
        data = os.path.join(self._td.name, "o")
        rc, _, err = orgfs("get", self.w.decl, a, data, "--spoke", "priv")
        self.assertNotEqual(rc, 0)
        self.assertEqual(orgfs("get", self.w.decl, a, data, "--spoke", "pub")[0], 0)
        self.assertEqual(orgfs("verify", self.w.decl, "--spoke", "pub")[0], 0)
        self.assertIn("no objects", orgfs("verify", self.w.decl, "--spoke", "priv")[1])

    def test_sealed_spoke_still_refuses_put(self):
        data = os.path.join(self._td.name, "x")
        with open(data, "wb") as fh:
            fh.write(b"abc")
        rc, _, err = orgfs("put", self.w.decl, data, "--spoke", "sealed")
        self.assertNotEqual(rc, 0)
        self.assertIn("sealed", err)

    def test_declaration_errors(self):
        text = slurp(self.w.decl)
        p = os.path.join(self._td.name, "bad.org")
        cases = {
            "member before any spoke": "** member lone\n:PROPERTIES:\n:ROOT: /x\n:TRANSPORT: local\n:END:\n" + text,
            "duplicate spoke": text + "\n* spoke pub\n:PROPERTIES:\n:RING: GRAIN\n:STRIPES: 1\n:REPLICAS: 1\n:VISIBILITY: public\n:END:\n** member z\n:PROPERTIES:\n:ROOT: /z\n:TRANSPORT: local\n:END:\n",
            "spoke without members": text + "\n* spoke empty\n:PROPERTIES:\n:RING: GRAIN\n:STRIPES: 1\n:REPLICAS: 1\n:VISIBILITY: public\n:END:\n",
            "member after a prose heading": text.replace("* spoke sealed", "* A prose heading\n* spoke sealed", 1).replace("* spoke priv", "* spoke priv\n* Interlude", 1),
        }
        for label, t in cases.items():
            with open(p, "w") as f:
                f.write(t)
            self.assertNotEqual(orgfs("check", p, "--no-mounts")[0], 0, label)

    def test_a_second_complete_spoke_is_accepted(self):
        text = slurp(self.w.decl) + "\n* spoke extra\n:PROPERTIES:\n:RING: GRAND\n:STRIPES: 1\n:REPLICAS: 1\n:VISIBILITY: private\n:END:\n** member h9\n:PROPERTIES:\n:ROOT: %s\n:TRANSPORT: local\n:END:\n" % self._td.name
        p = os.path.join(self._td.name, "ok.org")
        with open(p, "w") as f:
            f.write(text)
        rc, out, err = orgfs("check", p)
        self.assertEqual(rc, 0, err)
        self.assertIn("spoke\textra", out)


class MutatedWorldTests(unittest.TestCase):
    """Things that go wrong in a store; each gets a gap record, not a crash or a silent drop."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.w = World(self._td.name)
        self.o = self.w.put("pub", os.urandom(900))

    def view(self, viewer="h1", *extra):
        rc, out, err = orgfs("materialize", self.w.decl, "--as", viewer, *extra)
        self.assertEqual(rc, 0, err)
        return out

    def test_a_member_store_that_is_down_becomes_a_gap(self):
        os.rename(self.w.roots[("pub", "h4")], self.w.roots[("pub", "h4")] + ".gone")
        out = self.view("h1")
        self.assertIn("pub/member-store/h4", gap_ids(out))
        g = [g for g in gaps_of(out) if prop(g, "GAP_ID") == "pub/member-store/h4"][0]
        self.assertIn("not a directory", prop(g, "HOW"))
        check_gap_records(self, self.w, out)

    def test_disagreeing_manifests_become_a_gap(self):
        mp = os.path.join(self.w.roots[("pub", "h3")], ".orgfs", "pub", self.o, "MANIFEST")
        with open(mp, "a") as f:
            f.write("extra\n")
        out = self.view("h1")
        self.assertIn("pub/manifest/" + self.o, gap_ids(out))
        self.assertNotIn("slice-bytes/" + self.o, " ".join(gap_ids(out)))
        check_gap_records(self, self.w, out)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores directory modes")
    def test_an_unwritable_store_becomes_a_backlink_gap_and_no_backlink(self):
        src = None
        for h in SPOKES["pub"]["members"]:
            d = os.path.join(self.w.roots[("pub", h)], ".orgfs", "pub", self.o)
            if os.path.isdir(d):
                src = (h, d)
                break
        os.chmod(src[1], 0o555)
        self.addCleanup(os.chmod, src[1], 0o755)
        out = self.view("h1")
        self.assertIn("pub/backlink/%s/%s" % (self.o, src[0]), gap_ids(out))
        self.assertNotIn(src[1] + "/MATERIALIZED", out)
        check_gap_records(self, self.w, out)

    def test_deleting_the_source_record_breaks_the_trace(self):
        out = os.path.join(self._td.name, "v.org")
        orgfs("materialize", self.w.decl, "--as", "h1", "--out", out)
        self.assertEqual(orgfs("trace", out)[0], 0)
        for r in parse_org(slurp(out)):
            for bl in props(r, "BACKLINK"):
                os.remove(bl)
        rc, so, _ = orgfs("trace", out)
        self.assertNotEqual(rc, 0)
        self.assertIn("BROKEN", so)

    def test_a_record_for_another_host_does_not_satisfy_the_trace(self):
        out = os.path.join(self._td.name, "v.org")
        orgfs("materialize", self.w.decl, "--as", "h1", "--out", out)
        for r in parse_org(slurp(out)):
            for bl in props(r, "BACKLINK"):
                with open(bl) as f:
                    t = f.read()
                with open(bl, "w") as f:
                    f.write(t.replace("\th1\t", "\th2\t"))
        self.assertNotEqual(orgfs("trace", out)[0], 0)


if __name__ == "__main__":
    unittest.main(verbosity=1)
