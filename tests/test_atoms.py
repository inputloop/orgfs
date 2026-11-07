#!/usr/bin/env python3
# Copyright 2026 Corwin Brust <corwin@bru.st> and Contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for atoms and composition: compose, decompose, the round trip, one identity in both
forms, gap records that name atoms, and the manifest that keeps a composed file honest.
Python 3 standard library only.

The expectations are worked out from README.org ("Atoms and composition"): the layout, the
marker lines, what the manifest holds.  The manifest is also computed here, independently,
with hashlib, so a fault in the tool's hashing cannot hide behind itself.
"""
import hashlib
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
sha = T.sha
SPOKES = V.SPOKES
PUB3 = b"* spoke pub\n:PROPERTIES:\n:RING: GREAT\n:STRIPES: 3"
PUB4 = b"* spoke pub\n:PROPERTIES:\n:RING: GREAT\n:STRIPES: 4"


def read(p):
    with open(p, "rb") as f:
        return f.read()


def write(p, data):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode())


def tree(d):
    """Every file under d, as {relative path: bytes}."""
    out = {}
    for base, _, files in os.walk(d):
        for n in files:
            p = os.path.join(base, n)
            out[os.path.relpath(p, d).replace(os.sep, "/")] = read(p)
    return out


class AtomWorld:
    """The three spokes of test_views as atoms, over real local directories; and the file composed from them."""

    def __init__(self, d, prose="Prose atom.\n"):
        self.d = d
        self.atoms = os.path.join(d, "w.d")
        self.composed = os.path.join(d, "w.org")
        self.roots = {}
        for s, sp in SPOKES.items():
            for h in sp["members"]:
                self.roots[(s, h)] = os.path.join(d, "store", s, h)
                os.makedirs(self.roots[(s, h)])
        write(os.path.join(self.atoms, "preface.org"), "#+TITLE: atom world\n\n" + prose)
        for s, sp in SPOKES.items():
            write(os.path.join(self.atoms, s, "spoke.org"),
                  "* spoke %s\n:PROPERTIES:\n:RING: %s\n:STRIPES: %d\n:REPLICAS: %d\n:VISIBILITY: %s\n:END:\n"
                  % (s, sp["ring"], sp["stripes"], sp["replicas"], sp["vis"]))
            for h in sp["members"]:
                write(os.path.join(self.atoms, s, "member-%s.org" % h),
                      "** member %s\n:PROPERTIES:\n:ROOT: %s\n:TRANSPORT: local\n:END:\n" % (h, self.roots[(s, h)]))
        self.compose()

    def compose(self):
        rc, out, err = orgfs("compose", self.atoms, "--out", self.composed)
        assert rc == 0, err

    def put(self, spoke, data, name="in"):
        p = os.path.join(self.d, name)
        write(p, data)
        rc, out, err = orgfs("put", self.atoms, p, "--spoke", spoke)
        assert rc == 0, err
        return out.strip()


class Fresh(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.w = AtomWorld(self._td.name)


class ComposeTests(Fresh):
    def test_the_composed_file_holds_every_atom_between_markers(self):
        text = read(self.w.composed).decode()
        atoms = tree(self.w.atoms)
        self.assertEqual(len(atoms), 1 + sum(1 + len(sp["members"]) for sp in SPOKES.values()))
        for path, data in atoms.items():
            block = "# orgfs-atom: begin %s\n%s# orgfs-atom: end %s\n" % (path, data.decode(), path)
            self.assertEqual(text.count(block), 1, path)

    def test_spokes_come_in_name_order_and_spoke_org_before_its_members(self):
        begins = re.findall(r"(?m)^# orgfs-atom: begin (\S+)$", read(self.w.composed).decode())
        self.assertEqual(begins[0], "preface.org")
        self.assertEqual(begins[1:], sorted(begins[1:], key=lambda p: (p.split("/")[0], p.split("/")[1] != "spoke.org", p)))

    def test_compose_to_stdout_equals_compose_to_a_file(self):
        rc, out, err = orgfs("compose", self.w.atoms)
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.encode(), read(self.w.composed))

    def test_composing_twice_gives_the_same_bytes(self):
        a = read(self.w.composed)
        self.w.compose()
        self.assertEqual(read(self.w.composed), a)

    def test_the_manifest_is_the_identity_and_the_hash_of_every_atom(self):
        text = read(self.w.composed).decode()
        rc, out, _ = orgfs("check", self.w.atoms, "--no-mounts")
        ident = re.search(r"(?m)^file-identity\t(\w+)$", out).group(1)
        self.assertEqual(re.findall(r"(?m)^# orgfs-manifest: identity (\w+)$", text), [ident])
        want = {p: hashlib.sha256(d).hexdigest() for p, d in tree(self.w.atoms).items()}
        got = {p: h for h, p in re.findall(r"(?m)^# orgfs-manifest: sha256 (\w{64}) (\S+)$", text)}
        self.assertEqual(got, want)

    def test_compose_refuses_an_atom_without_a_final_newline(self):
        p = os.path.join(self.w.atoms, "preface.org")
        write(p, "no newline at the end")
        rc, out, err = orgfs("compose", self.w.atoms)
        self.assertNotEqual(rc, 0)
        self.assertIn("preface.org", err)
        self.assertIn("newline", err)

    def test_compose_refuses_an_atom_that_holds_a_marker(self):
        # a well-formed end marker: without the refusal it would close the atom early and compose would succeed
        write(os.path.join(self.w.atoms, "preface.org"), "first\n# orgfs-atom: end preface.org\nsecond\n")
        rc, out, err = orgfs("compose", self.w.atoms)
        self.assertNotEqual(rc, 0)
        self.assertIn("composition marker", err)

    def test_compose_refuses_an_atom_in_the_wrong_place(self):
        write(os.path.join(self.w.atoms, "pub", "deeper", "x.org"), "x\n")
        rc, out, err = orgfs("compose", self.w.atoms)
        self.assertNotEqual(rc, 0)
        self.assertIn("pub/deeper/x.org", err)

    def test_compose_refuses_an_out_file_inside_the_atoms(self):
        rc, out, err = orgfs("compose", self.w.atoms, "--out", os.path.join(self.w.atoms, "all.org"))
        self.assertNotEqual(rc, 0)
        self.assertFalse(os.path.exists(os.path.join(self.w.atoms, "all.org")))

    def test_compose_refuses_atoms_that_declare_nothing_valid(self):
        write(os.path.join(self.w.atoms, "pub", "member-h9.org"), "** member h1\n:PROPERTIES:\n:ROOT: /x\n:TRANSPORT: local\n:END:\n")
        rc, out, err = orgfs("compose", self.w.atoms)
        self.assertNotEqual(rc, 0)
        self.assertIn("member-h9.org", err)

    def test_a_non_org_file_among_the_atoms_is_named_and_left_out(self):
        write(os.path.join(self.w.atoms, "NOTES.txt"), "hello\n")
        rc, out, err = orgfs("compose", self.w.atoms)
        self.assertEqual(rc, 0, err)
        self.assertIn("NOTES.txt", err)
        self.assertNotIn("hello", out)


class RoundTripTests(Fresh):
    def decompose_to(self, name="back"):
        back = os.path.join(self._td.name, name)
        rc, out, err = orgfs("decompose", self.w.composed, back)
        self.assertEqual(rc, 0, err)
        return back

    def test_atoms_compose_decompose_atoms_byte_for_byte(self):
        back = self.decompose_to()
        self.assertEqual(tree(back), tree(self.w.atoms))

    def test_hard_bytes_survive_the_round_trip(self):
        write(os.path.join(self.w.atoms, "preface.org"), b"crlf line\r\n\xc3\xa9\xff\x01 tab\t \n\n\n   \n")
        write(os.path.join(self.w.atoms, "empty.org"), b"")
        self.w.compose()
        back = self.decompose_to()
        self.assertEqual(tree(back), tree(self.w.atoms))
        self.assertEqual(read(os.path.join(back, "empty.org")), b"")

    def test_decompose_then_compose_gives_the_composed_file_again(self):
        back = self.decompose_to()
        again = os.path.join(self._td.name, "again.org")
        self.assertEqual(orgfs("compose", back, "--out", again)[0], 0)
        self.assertEqual(read(again), read(self.w.composed))

    def test_decompose_splits_one_file_per_atom_and_never_merges(self):
        back = self.decompose_to()
        self.assertEqual(sorted(tree(back)), sorted(tree(self.w.atoms)))
        for p, data in tree(back).items():
            self.assertEqual(data.count(b"** member ") + data.count(b"* spoke "), 1 if p != "preface.org" else 0, p)

    def test_decompose_again_into_the_same_directory_is_idempotent(self):
        back = self.decompose_to()
        rc, out, err = orgfs("decompose", self.w.composed, back)
        self.assertEqual(rc, 0, err)

    def test_decompose_refuses_to_overwrite_an_atom_that_differs_and_writes_nothing(self):
        back = self.decompose_to()
        write(os.path.join(back, "pub", "spoke.org"), "changed here\n")
        os.remove(os.path.join(back, "preface.org"))
        rc, out, err = orgfs("decompose", self.w.composed, back)
        self.assertNotEqual(rc, 0)
        self.assertIn("pub/spoke.org", err)
        self.assertFalse(os.path.exists(os.path.join(back, "preface.org")))
        self.assertEqual(read(os.path.join(back, "pub", "spoke.org")), b"changed here\n")

    def test_decompose_names_an_atom_that_is_in_the_directory_but_not_the_file(self):
        back = self.decompose_to()
        write(os.path.join(back, "extra.org"), "x\n")
        rc, out, err = orgfs("decompose", self.w.composed, back)
        self.assertEqual(rc, 0, err)
        self.assertIn("extra.org", err)

    def test_decompose_refuses_text_outside_the_atoms(self):
        p = os.path.join(self._td.name, "loose.org")
        write(p, read(self.w.composed) + b"\n* spoke sneaky\n")
        rc, out, err = orgfs("decompose", p, os.path.join(self._td.name, "x"))
        self.assertNotEqual(rc, 0)
        self.assertIn("outside any atom", err)
        self.assertFalse(os.path.exists(os.path.join(self._td.name, "x")))

    def test_decompose_refuses_a_path_that_climbs_out(self):
        p = os.path.join(self._td.name, "climb.org")
        write(p, "# orgfs-atom: begin ../evil.org\nx\n# orgfs-atom: end ../evil.org\n")
        rc, out, err = orgfs("decompose", p, os.path.join(self._td.name, "x"))
        self.assertNotEqual(rc, 0)
        self.assertFalse(os.path.exists(os.path.join(self._td.name, "evil.org")))

    def test_decompose_refuses_an_unclosed_a_mismatched_and_a_repeated_atom(self):
        cases = {
            "unclosed": "# orgfs-atom: begin a.org\nx\n",
            "mismatch": "# orgfs-atom: begin a.org\nx\n# orgfs-atom: end b.org\n",
            "twice": "# orgfs-atom: begin a.org\nx\n# orgfs-atom: end a.org\n# orgfs-atom: begin a.org\ny\n# orgfs-atom: end a.org\n",
            "nested": "# orgfs-atom: begin a.org\n# orgfs-atom: begin b.org\n# orgfs-atom: end b.org\n# orgfs-atom: end a.org\n",
        }
        for name, text in cases.items():
            p = os.path.join(self._td.name, name + ".org")
            write(p, text)
            rc, out, err = orgfs("decompose", p, os.path.join(self._td.name, "out-" + name))
            self.assertNotEqual(rc, 0, name)
            self.assertFalse(os.path.exists(os.path.join(self._td.name, "out-" + name)), name)


class OneIdentityTests(Fresh):
    def test_check_prints_the_same_in_both_forms(self):
        a = orgfs("check", self.w.atoms, "--no-mounts")
        b = orgfs("check", self.w.composed, "--no-mounts")
        self.assertEqual(a[0], 0, a[2])
        self.assertEqual(a, b)
        self.assertEqual(len(re.findall(r"(?m)^identity\t", a[1])), 3)

    def test_the_identity_is_the_one_a_second_implementation_computes(self):
        out = orgfs("check", self.w.composed, "--no-mounts")[1]
        want = [V.ref_spoke_identity(s, SPOKES[s], {h: self.w.roots[(s, h)] for h in SPOKES[s]["members"]}) for s in sorted(SPOKES)]
        self.assertEqual(re.findall(r"(?m)^identity\t(\w+)$", out), want)
        self.assertEqual(re.search(r"(?m)^file-identity\t(\w+)$", out).group(1), V.ref_file_identity(want))

    def test_plan_is_the_same_in_both_forms(self):
        for s in SPOKES:
            self.assertEqual(orgfs("plan", self.w.atoms, "--spoke", s), orgfs("plan", self.w.composed, "--spoke", s), s)

    def test_agree_accepts_the_atoms_against_the_composed_file(self):
        rc, out, err = orgfs("agree", self.w.atoms, self.w.composed)
        self.assertEqual(rc, 0, err)
        self.assertIn("agree: OK", out)

    def test_the_licensing_example_keeps_its_0_1_0_identity_in_both_forms(self):
        atoms = os.path.join(T.ROOT_DIR, "examples", "licensing.d")
        comp = os.path.join(self._td.name, "licensing.org")
        self.assertEqual(orgfs("compose", atoms, "--out", comp)[0], 0)
        for decl in (atoms, comp):
            out = orgfs("check", decl, "--no-mounts")[1]
            self.assertIn("identity\t1df8d78c7e2b291b459ee275c505504e655e75ee2e1693eeabe98e23c319e013", out)

    def test_prose_atoms_and_their_order_do_not_change_the_identity(self):
        base = orgfs("check", self.w.atoms, "--no-mounts")[1]
        write(os.path.join(self.w.atoms, "preface.org"), "#+TITLE: other\n\nAnother paragraph.\n* A heading\nwords\n")
        write(os.path.join(self.w.atoms, "zzz-more-prose.org"), "More.\n")
        self.assertEqual(orgfs("check", self.w.atoms, "--no-mounts")[1], base)

    def test_spoke_blocks_in_a_composed_file_may_be_in_any_order(self):
        base = orgfs("check", self.w.composed, "--no-mounts")[1]
        text = read(self.w.composed).decode()
        blocks = re.findall(r"(?ms)^# orgfs-atom: begin (\S+)\n.*?^# orgfs-atom: end \1\n", text)
        by_spoke = {}
        for m in re.finditer(r"(?ms)^# orgfs-atom: begin (\S+)\n.*?^# orgfs-atom: end \1\n", text):
            by_spoke.setdefault(m.group(1).split("/")[0], []).append(m.group(0))
        self.assertEqual(len(blocks), sum(len(v) for v in by_spoke.values()))
        shuffled = "".join("\n" + "".join(by_spoke[k]) for k in ("sealed", "pub", "priv", "preface.org"))
        p = os.path.join(self._td.name, "shuffled.org")
        write(p, shuffled)
        out = orgfs("check", p, "--no-mounts")
        self.assertEqual(out[0], 0, out[2])
        self.assertEqual(re.findall(r"file-identity\t\w+", out[1]), re.findall(r"file-identity\t\w+", base))

    def test_changing_one_atom_changes_only_its_spoke(self):
        before = re.findall(r"(?m)^identity\t(\w+)$", orgfs("check", self.w.atoms, "--no-mounts")[1])
        p = os.path.join(self.w.atoms, "pub", "spoke.org")
        write(p, read(p).replace(b":STRIPES: 3", b":STRIPES: 4"))
        after = re.findall(r"(?m)^identity\t(\w+)$", orgfs("check", self.w.atoms, "--no-mounts")[1])
        self.assertEqual([a == b for a, b in zip(before, after)], [True, False, True])   # priv, pub, sealed

    def test_materialize_and_assemble_give_the_same_view_in_both_forms(self):
        self.w.put("pub", os.urandom(1500), "a")
        self.w.put("priv", b"private bytes", "b")
        for viewer in ("out", "h1", "h2", "h5"):
            a = orgfs("materialize", self.w.atoms, "--as", viewer)
            b = orgfs("materialize", self.w.composed, "--as", viewer)
            self.assertEqual(a[0], 0, a[2])
            self.assertEqual(a[1], b[1], viewer)
            self.assertEqual(len(V.gaps_of(a[1])) > 0, True)

    def test_a_store_written_through_the_atoms_is_read_through_the_composed_file(self):
        data = os.urandom(777)
        oid = self.w.put("pub", data)
        out = os.path.join(self._td.name, "got")
        rc, o, err = orgfs("get", self.w.composed, oid, out, "--spoke", "pub")
        self.assertEqual(rc, 0, err)
        self.assertEqual(read(out), data)
        self.assertEqual(orgfs("verify", self.w.composed, "--spoke", "pub")[0], 0)


class GapAtomTests(Fresh):
    def setUp(self):
        super().setUp()
        self.w.put("pub", os.urandom(900), "a")
        self.w.put("priv", b"secret", "b")
        self.view = orgfs("materialize", self.w.atoms, "--as", "out")[1]

    def locate(self, where):
        """'ATOM line N (:KEY:)' -> (atom path, N, KEY), checked against the atom file itself."""
        m = re.fullmatch(r"(\S+\.org) line (\d+) \(:([A-Z]+):\)", where)
        self.assertIsNotNone(m, where)
        path, n, key = m.group(1), int(m.group(2)), m.group(3)
        lines = read(os.path.join(self.w.atoms, path)).decode().split("\n")
        self.assertTrue(lines[n - 1].strip().upper().startswith(":%s:" % key), (where, lines[n - 1]))
        return path, n, key

    def test_rule_at_names_the_atom_and_the_line_in_it(self):
        n = 0
        for g in V.gaps_of(self.view):
            at = V.prop(g, "RULE_AT")
            if at.startswith("not in the declaration"):
                continue
            for part in at.split("; withheld field declared at "):
                for piece in part.split(", "):
                    self.locate(piece)
                    n += 1
        self.assertGreater(n, 0)

    def test_rule_at_never_points_into_the_composed_file(self):
        comp = read(self.w.composed).decode().split("\n")
        for g in V.gaps_of(self.view):
            at = V.prop(g, "RULE_AT")
            self.assertNotIn("w.org", at)
            self.assertNotRegex(at, r"^line \d+")
            for piece in re.findall(r"(\S+\.org) line (\d+)", at):
                self.assertTrue(os.path.exists(os.path.join(self.w.atoms, piece[0])), at)
                self.assertLess(int(piece[1]), 10, at)   # an atom is a few lines; a composed line would be well past 10
        self.assertGreater(len(comp), 30)

    def test_the_member_root_gap_names_two_atoms(self):
        g = [g for g in V.gaps_of(self.view) if V.prop(g, "GAP_ID") == "priv/member-root/h3"][0]
        at = V.prop(g, "RULE_AT")
        self.assertRegex(at, r"^priv/spoke\.org line 6 \(:VISIBILITY:\); withheld field declared at priv/member-h3\.org line 3 \(:ROOT:\)$")
        self.locate(at.split("; withheld field declared at ")[0])
        self.locate(at.split("; withheld field declared at ")[1])

    def test_gap_records_are_the_same_whether_the_declaration_was_atoms_or_composed(self):
        a = V.gaps_of(self.view)
        b = V.gaps_of(orgfs("materialize", self.w.composed, "--as", "out")[1])
        self.assertEqual([r["props"] for r in a], [r["props"] for r in b])

    def test_the_slice_gap_cites_the_atom_that_states_stripes_and_replicas(self):
        view = orgfs("materialize", self.w.atoms, "--as", "h5")[1]
        ats = [V.prop(g, "RULE_AT") for g in V.gaps_of(view) if V.prop(g, "WHAT_KIND") == "slice-bytes"
               and not V.prop(g, "RULE_AT").startswith("not in the declaration")]
        self.assertTrue(ats)
        for at in ats:
            self.assertRegex(at, r"^pub/spoke\.org line \d+ \(:STRIPES:\), pub/spoke\.org line \d+ \(:REPLICAS:\)$")
            for piece in at.split(", "):
                self.locate(piece)

    def test_a_backlink_gap_names_the_member_atom(self):
        # make one store directory unwritable-looking by removing it from under the view: use a read-only member
        import stat
        root = os.path.join(self.w.roots[("pub", "h1")], ".orgfs", "pub")
        objs = os.listdir(root)
        for o in objs:
            os.chmod(os.path.join(root, o), stat.S_IRUSR | stat.S_IXUSR)
        try:
            if os.access(os.path.join(root, objs[0]), os.W_OK):
                self.skipTest("running as a user that can write to read-only directories")
            view = orgfs("materialize", self.w.atoms, "--as", "out")[1]
        finally:
            for o in objs:
                os.chmod(os.path.join(root, o), stat.S_IRWXU)
        gs = [g for g in V.gaps_of(view) if V.prop(g, "WHAT_KIND") == "backlink" and "/h1" in V.prop(g, "GAP_ID")]
        self.assertTrue(gs)
        for g in gs:
            self.assertEqual(V.prop(g, "RULE_AT").split(" line ")[0], "pub/member-h1.org")
            self.locate(V.prop(g, "RULE_AT"))

    def test_displayed_headings_say_which_atom_declared_them(self):
        for r in V.parse_org(self.view):
            if r["title"].startswith("spoke ") or r["title"].startswith("member "):
                at = V.prop(r, "DECLARED_AT")
                m = re.fullmatch(r"(\S+\.org) line (\d+)", at)
                self.assertIsNotNone(m, (r["title"], at))
                first = read(os.path.join(self.w.atoms, m.group(1))).decode().split("\n")[int(m.group(2)) - 1]
                self.assertEqual(first, ("* " if r["level"] == 1 else "** ") + r["title"])

    def test_a_plain_single_file_declaration_still_cites_plain_lines(self):
        w = V.World(os.path.join(self._td.name, "plain"))
        w.fill()
        text = orgfs("materialize", w.decl, "--as", "out")[1]
        for g in V.gaps_of(text):
            if not V.prop(g, "RULE_AT").startswith("not in the declaration"):
                self.assertRegex(V.prop(g, "RULE_AT"), r"^line \d+ ")


class BadAtomTests(Fresh):
    def refused(self, why):
        rc, out, err = orgfs("check", self.w.atoms, "--no-mounts")
        self.assertNotEqual(rc, 0)
        self.assertIn(why, err)

    def test_a_member_atom_whose_heading_names_another_host(self):
        p = os.path.join(self.w.atoms, "pub", "member-h1.org")
        write(p, read(p).replace(b"member h1", b"member h9"))
        self.refused("member-h9.org")

    def test_a_spoke_atom_that_names_another_spoke(self):
        p = os.path.join(self.w.atoms, "pub", "spoke.org")
        write(p, read(p).replace(b"* spoke pub", b"* spoke zzz"))
        self.refused("pub/spoke.org")

    def test_a_spoke_atom_with_two_spokes(self):
        p = os.path.join(self.w.atoms, "pub", "spoke.org")
        write(p, read(p) + read(os.path.join(self.w.atoms, "priv", "spoke.org")))
        self.refused("spoke.org")

    def test_a_spoke_declared_in_a_prose_atom(self):
        write(os.path.join(self.w.atoms, "notes.org"), "* spoke loose\n:PROPERTIES:\n:RING: GRAIN\n:END:\n")
        self.refused("notes.org")

    def test_a_member_atom_with_no_member_in_it(self):
        write(os.path.join(self.w.atoms, "pub", "member-h1.org"), "nothing here\n")
        self.refused("member-h1.org")

    def test_a_composed_file_with_a_heading_outside_its_atoms(self):
        p = os.path.join(self._td.name, "mix.org")
        write(p, read(self.w.composed) + b"\n* spoke loose\n")
        rc, out, err = orgfs("check", p, "--no-mounts")
        self.assertNotEqual(rc, 0)
        self.assertIn("outside any atom", err)

    def test_an_atom_error_names_the_atom_and_its_line(self):
        p = os.path.join(self.w.atoms, "pub", "spoke.org")
        write(p, read(p).replace(b":RING: GREAT", b":RING: GREAT\n:BOGUS: 1"))
        rc, out, err = orgfs("check", self.w.atoms, "--no-mounts")
        self.assertNotEqual(rc, 0)
        self.assertRegex(err, r"pub/spoke\.org:\d+: unknown spoke property BOGUS")


class ManifestTests(Fresh):
    """A composed file may be depended on only if reading it always pulls in the atoms."""

    def read_every_way(self):
        return [("check", self.w.composed, "--no-mounts"),
                ("plan", self.w.composed, "--spoke", "pub"),
                ("materialize", self.w.composed, "--as", "out"),
                ("agree", self.w.composed, self.w.atoms)]

    def test_a_fresh_composed_file_is_read_without_complaint(self):
        for args in self.read_every_way():
            rc, out, err = orgfs(*args)
            self.assertEqual(rc, 0, (args, err))
            self.assertNotIn("STALE", err)
            self.assertNotIn("freshness not checked", err)

    def test_a_changed_atom_makes_every_command_refuse(self):
        p = os.path.join(self.w.atoms, "pub", "spoke.org")
        write(p, read(p).replace(b":STRIPES: 3", b":STRIPES: 4"))
        for args in self.read_every_way():
            rc, out, err = orgfs(*args)
            self.assertNotEqual(rc, 0, args)
            self.assertIn("STALE: pub/spoke.org changed since compose", err, args)
            self.assertEqual(out.count("identity"), 0, args)   # nothing was quietly printed from the old text

    def test_a_changed_prose_atom_also_refuses(self):
        p = os.path.join(self.w.atoms, "preface.org")
        write(p, read(p) + b"one more line\n")
        rc, out, err = orgfs("check", self.w.composed, "--no-mounts")
        self.assertNotEqual(rc, 0)
        self.assertIn("STALE: preface.org changed since compose", err)

    def test_an_added_atom_not_in_the_manifest_refuses(self):
        write(os.path.join(self.w.atoms, "pub", "member-h9.org"),
              "** member h9\n:PROPERTIES:\n:ROOT: /tmp/h9\n:TRANSPORT: local\n:END:\n")
        for args in self.read_every_way():
            rc, out, err = orgfs(*args)
            self.assertNotEqual(rc, 0, args)
            self.assertIn("STALE: pub/member-h9.org added since compose", err, args)

    def test_a_removed_atom_refuses(self):
        os.remove(os.path.join(self.w.atoms, "pub", "member-h4.org"))
        rc, out, err = orgfs("plan", self.w.composed, "--spoke", "pub")
        self.assertNotEqual(rc, 0)
        self.assertIn("STALE: pub/member-h4.org removed since compose", err)

    def test_every_stale_atom_is_named_not_just_the_first(self):
        write(os.path.join(self.w.atoms, "preface.org"), "changed\n")
        write(os.path.join(self.w.atoms, "extra.org"), "added\n")
        os.remove(os.path.join(self.w.atoms, "priv", "member-h2.org"))
        err = orgfs("check", self.w.composed, "--no-mounts")[2]
        for line in ("preface.org changed", "extra.org added", "priv/member-h2.org removed"):
            self.assertIn("STALE: " + line + " since compose", err)

    def test_composing_again_makes_it_fresh(self):
        p = os.path.join(self.w.atoms, "pub", "spoke.org")
        write(p, read(p).replace(b":STRIPES: 3", b":STRIPES: 4"))
        self.assertNotEqual(orgfs("check", self.w.composed, "--no-mounts")[0], 0)
        self.w.compose()
        rc, out, err = orgfs("check", self.w.composed, "--no-mounts")
        self.assertEqual(rc, 0, err)
        self.assertEqual(out, orgfs("check", self.w.atoms, "--no-mounts")[1])

    def test_a_composed_file_edited_in_place_is_refused(self):
        text = read(self.w.composed)
        self.assertIn(PUB3, text)
        write(self.w.composed, text.replace(PUB3, PUB4, 1))
        os.rename(self.w.atoms, self.w.atoms + ".aside")   # the atoms are not beside it any more
        rc, out, err = orgfs("check", self.w.composed, "--no-mounts")
        self.assertNotEqual(rc, 0)
        self.assertIn("STALE: pub/spoke.org changed since compose", err)

    def test_a_composed_file_that_travelled_alone_is_read_and_says_so(self):
        lone = os.path.join(self._td.name, "lone")
        os.makedirs(lone)
        dst = os.path.join(lone, "w.org")
        write(dst, read(self.w.composed))
        rc, out, err = orgfs("check", dst, "--no-mounts")
        self.assertEqual(rc, 0, err)
        self.assertIn("composed file read without its atoms; freshness not checked", err)
        self.assertEqual(re.findall(r"file-identity\t\w+", out), re.findall(r"file-identity\t\w+", orgfs("check", self.w.atoms, "--no-mounts")[1]))

    def test_the_manifest_identity_must_match_what_the_file_declares(self):
        lone = os.path.join(self._td.name, "lone2")
        os.makedirs(lone)
        dst = os.path.join(lone, "w.org")
        text = read(self.w.composed)
        m = re.search(rb"# orgfs-manifest: identity (\w{64})", text)
        write(dst, text.replace(m.group(1), b"0" * 64))
        rc, out, err = orgfs("check", dst, "--no-mounts")
        self.assertNotEqual(rc, 0)
        self.assertIn("STALE", err)

    def test_a_composed_file_without_a_manifest_is_read_and_says_so(self):
        p = os.path.join(self._td.name, "nomf.org")
        text = re.sub(rb"(?m)^# orgfs-manifest: .*\n", b"", read(self.w.composed))
        write(p, text)
        rc, out, err = orgfs("check", p, "--no-mounts")
        self.assertEqual(rc, 0, err)
        self.assertIn("no manifest", err)

    def test_a_plain_declaration_with_no_atoms_has_nothing_to_go_stale(self):
        w = V.World(os.path.join(self._td.name, "plain"))
        rc, out, err = orgfs("check", w.decl, "--no-mounts")
        self.assertEqual(rc, 0, err)
        self.assertEqual(err, "")

    def test_decompose_of_an_edited_composed_file_still_works(self):
        text = read(self.w.composed)
        write(self.w.composed, text.replace(PUB3, PUB4, 1))
        back = os.path.join(self._td.name, "back")
        rc, out, err = orgfs("decompose", self.w.composed, back)
        self.assertEqual(rc, 0, err)
        self.assertIn(b":STRIPES: 4", read(os.path.join(back, "pub", "spoke.org")))


class DemoTests(unittest.TestCase):
    def test_the_demo_composes_checks_both_forms_and_still_ends_well(self):
        rc, out, err = orgfs("demo")
        self.assertEqual(rc, 0, err)
        self.assertIn("same file identity from the atoms in world.d and from world.org: yes", out)
        self.assertIn("composing the atoms again gives the same bytes: yes", out)


if __name__ == "__main__":
    unittest.main()
