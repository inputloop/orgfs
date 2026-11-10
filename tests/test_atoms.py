#!/usr/bin/env python3
# Copyright 2026 Corwin Brust <corwin@bru.st> and Bryant Frankford (INPUTLOOP)
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for atoms and composition: the story and its #+INCLUDE lines, compose (expanded and source
form), expand, decompose, the round trip, one identity in every form, gap records that name atoms,
and the manifest that keeps a composed file honest.  Python 3 standard library only.

The expectations are worked out from README.org ("Org, not our own syntax", "Atoms and composition"):
the layout, the :ORGFS_ATOM: headings, what the manifest table holds.  The manifest is also computed
here, independently, with hashlib; and the #+INCLUDE expansion has a second implementation here
(ref_expand), written from the README's description of the subset, so a fault in the tool's own
expander cannot hide behind itself.  Emacs is the third reader: tests/org_check.el.
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
PUB3 = b":ORGFS_ATOM: pub/spoke.org\n:RING: GREAT\n:STRIPES: 3"
PUB4 = b":ORGFS_ATOM: pub/spoke.org\n:RING: GREAT\n:STRIPES: 4"


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


def atoms_of(d):
    return {p: b for p, b in tree(d).items() if p.endswith(".org") and p != "story.org"}


def prose_atom(path, title, text, level=1):
    return "%s %s\n:PROPERTIES:\n:ORGFS_ATOM: %s\n:END:\n%s" % ("*" * level, title, path, text)


def spoke_atom(s, sp, level=1):
    return ("%s spoke %s\n:PROPERTIES:\n:ORGFS_ATOM: %s/spoke.org\n:RING: %s\n:STRIPES: %d\n:REPLICAS: %d\n:VISIBILITY: %s\n:END:\n"
            % ("*" * level, s, s, sp["ring"], sp["stripes"], sp["replicas"], sp["vis"]))


def member_atom(s, h, root, level=1):
    return ("%s member %s\n:PROPERTIES:\n:ORGFS_ATOM: %s/member-%s.org\n:ROOT: %s\n:TRANSPORT: local\n:END:\n"
            % ("*" * level, h, s, h, root))


def default_order(paths):
    """The order the atoms follow when there is no story.org: prose atoms, then each spoke in name order, spoke.org first."""
    def key(p):
        parts = p.split("/")
        return (0, "", 0, p) if len(parts) == 1 else (1, parts[0], 0 if parts[1] == "spoke.org" else 1, p)
    return sorted(paths, key=key)


def ref_expand(path, induced=1, trim=False):
    """A second implementation of the #+INCLUDE subset, from the README: the one form
    #+INCLUDE: "relative/path.org"; an included file loses blank lines at head and tail; its headings
    move so the shallowest is one below the heading the include sits under (level 1 under none);
    includes inside it are expanded the same way, relative to it."""
    lines = read(path).decode().split("\n")
    assert lines[-1] == ""
    lines = lines[:-1]
    if trim:
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        stars = [len(re.match(r"\*+", l).group()) for l in lines if re.match(r"\*+ ", l)]
        if stars:
            off = induced - min(stars)
            moved = []
            for l in lines:
                if re.match(r"\*+ ", l):
                    l = "*" * off + l if off >= 0 else l[-off:]
                moved.append(l)
            lines = moved
    out, cur = [], 0
    for l in lines:
        m = re.match(r"(\*+) ", l)
        if m:
            cur = len(m.group(1))
        m = re.match(r'#\+INCLUDE: "([^"]+)"$', l)
        if m:
            out.append(ref_expand(os.path.join(os.path.dirname(path), m.group(1)), cur + 1, True))
        else:
            out.append(l + "\n")
    return "".join(out)


class AtomWorld:
    """The three spokes of test_views as atoms (and a story.org setting them in the default order),
    over real local directories; and the file composed from them."""

    def __init__(self, d, prose="Prose atom.\n"):
        self.d = d
        self.atoms = os.path.join(d, "w.d")
        self.composed = os.path.join(d, "w.org")
        self.roots = {}
        for s, sp in SPOKES.items():
            for h in sp["members"]:
                self.roots[(s, h)] = os.path.join(d, "store", s, h)
                os.makedirs(self.roots[(s, h)])
        write(os.path.join(self.atoms, "preface.org"), prose_atom("preface.org", "atom world", prose))
        for s, sp in SPOKES.items():
            write(os.path.join(self.atoms, s, "spoke.org"), spoke_atom(s, sp))
            for h in sp["members"]:
                write(os.path.join(self.atoms, s, "member-%s.org" % h), member_atom(s, h, self.roots[(s, h)]))
        self.write_story()
        self.compose()

    def write_story(self):
        order = default_order(atoms_of(self.atoms))
        write(os.path.join(self.atoms, "story.org"), "\n".join('#+INCLUDE: "%s"\n' % p for p in order))

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


def manifest_rows(text):
    """The rows of the table named orgfs-manifest, as {atom: sha256}."""
    m = re.search(r"(?m)^#\+NAME: orgfs-manifest\n((?:\|.*\n)+)", text)
    assert m, "no table named orgfs-manifest"
    rows = {}
    for line in m.group(1).splitlines():
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) == 2 and re.fullmatch(r"[0-9a-f]{64}", cells[1]):
            rows[cells[0]] = cells[1]
    return rows


class ComposeTests(Fresh):
    def test_the_composed_file_holds_every_atom_whole_and_marks_it_with_a_property(self):
        text = read(self.w.composed).decode()
        atoms = atoms_of(self.w.atoms)
        self.assertEqual(len(atoms), 1 + sum(1 + len(sp["members"]) for sp in SPOKES.values()))
        for path, data in atoms.items():
            self.assertEqual(text.count(data.decode()), 1, path)
            self.assertEqual(text.count("\n:ORGFS_ATOM: %s\n" % path), 1, path)

    def test_no_marker_of_our_own_is_left_in_the_file(self):
        text = read(self.w.composed).decode()
        self.assertNotRegex(text, r"(?m)^#( |$)")                 # not even a comment line
        self.assertNotRegex(text, r"orgfs-(atom|compose)")
        self.assertNotRegex(text, r"(?m)^#\+(?!NAME: orgfs-manifest$)")   # the one keyword is the table's name

    def test_every_atom_begins_at_a_heading_with_its_property_in_the_first_drawer(self):
        text = read(self.w.composed).decode()
        n = 0
        for m in re.finditer(r"(?m)^:ORGFS_ATOM: (\S+)$", text):
            before = text[:m.start()].split("\n")
            self.assertEqual(before[-2], ":PROPERTIES:")
            self.assertRegex(before[-3], r"^\*+ ")
            n += 1
        self.assertEqual(n, len(atoms_of(self.w.atoms)))

    def test_atoms_come_in_the_story_s_order(self):
        order = re.findall(r"(?m)^:ORGFS_ATOM: (\S+)$", read(self.w.composed).decode())
        self.assertEqual(order, default_order(atoms_of(self.w.atoms)))
        self.assertEqual(order[0], "preface.org")

    def test_compose_to_stdout_equals_compose_to_a_file(self):
        rc, out, err = orgfs("compose", self.w.atoms)
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.encode(), read(self.w.composed))

    def test_composing_twice_gives_the_same_bytes(self):
        a = read(self.w.composed)
        self.w.compose()
        self.assertEqual(read(self.w.composed), a)

    def test_the_manifest_is_a_named_table_and_the_identity_a_property_of_its_heading(self):
        text = read(self.w.composed).decode()
        rc, out, _ = orgfs("check", self.w.atoms, "--no-mounts")
        ident = re.search(r"(?m)^file-identity\t(\w+)$", out).group(1)
        self.assertRegex(text, r"(?m)^\* Atoms and their hashes\n:PROPERTIES:\n:ORGFS_IDENTITY: %s\n:END:\n" % ident)
        self.assertIn("\n#+NAME: orgfs-manifest\n| atom | sha256 |\n", text)
        want = {p: hashlib.sha256(d).hexdigest() for p, d in atoms_of(self.w.atoms).items()}
        want["story.org"] = hashlib.sha256(read(os.path.join(self.w.atoms, "story.org"))).hexdigest()
        self.assertEqual(manifest_rows(text), want)

    def test_the_manifest_section_comes_last_and_says_what_it_is_for(self):
        text = read(self.w.composed).decode()
        head, _, tail = text.partition("\n* Atoms and their hashes\n")
        self.assertIn("This table says which atoms the document above was composed from", tail)
        self.assertNotRegex(tail, r"(?m)^\*+ ")   # nothing else is a heading after it

    def test_compose_refuses_an_atom_without_a_final_newline(self):
        p = os.path.join(self.w.atoms, "preface.org")
        write(p, read(p).rstrip(b"\n"))
        rc, out, err = orgfs("compose", self.w.atoms)
        self.assertNotEqual(rc, 0)
        self.assertIn("preface.org", err)
        self.assertIn("newline", err)

    def refuses(self, text, why, atom="preface.org"):
        write(os.path.join(self.w.atoms, atom), text)
        rc, out, err = orgfs("compose", self.w.atoms)
        self.assertNotEqual(rc, 0, why)
        self.assertIn(why, err)
        self.assertIn(atom, err)

    def test_compose_refuses_an_atom_that_holds_an_include(self):
        self.refuses(prose_atom("preface.org", "x", 'first\n#+INCLUDE: "other.org"\nsecond\n'), "no #+INCLUDE")

    def test_compose_refuses_an_atom_that_does_not_begin_with_a_heading(self):
        self.refuses("words first\n* then a heading\n:PROPERTIES:\n:ORGFS_ATOM: preface.org\n:END:\n", "must begin with a heading")

    def test_compose_refuses_an_atom_whose_property_names_another_path(self):
        self.refuses(prose_atom("elsewhere.org", "x", "t\n"), "says elsewhere.org")

    def test_compose_refuses_an_atom_with_no_property(self):
        self.refuses("* x\n:PROPERTIES:\n:CUSTOM_ID: a\n:END:\nt\n", "lacks")

    def test_compose_refuses_a_second_atom_heading_inside_an_atom(self):
        self.refuses(prose_atom("preface.org", "x", "t\n") + prose_atom("other.org", "y", "u\n", level=2), "second :ORGFS_ATOM:")

    def test_compose_refuses_an_atom_that_ends_in_a_blank_line(self):
        # Org drops blank lines around an include, so such an atom could not come back byte for byte
        self.refuses(prose_atom("preface.org", "x", "t\n\n"), "blank line")

    def test_compose_refuses_a_carriage_return_and_footnotes_and_file_links(self):
        for bad, why in (("a\r\n", "carriage return"), ("see[fn:1]\n", "footnote"), ("[[file:x.org]]\n", "file link"), ("[[./x]]\n", "file link")):
            self.refuses(prose_atom("preface.org", "x", bad), why)

    def test_compose_refuses_stars_inside_a_block(self):
        self.refuses(prose_atom("preface.org", "x", "#+BEGIN_QUOTE\n* not a heading\n#+END_QUOTE\n"), "inside a block")

    def test_the_word_file_in_prose_is_not_a_file_link(self):
        write(os.path.join(self.w.atoms, "preface.org"), prose_atom("preface.org", "x", "A profile: of the file named in the text.\n"))
        self.assertEqual(orgfs("compose", self.w.atoms)[0], 0)

    def test_compose_refuses_an_atom_in_the_wrong_place(self):
        write(os.path.join(self.w.atoms, "pub", "deeper", "x.org"), "x\n")
        rc, out, err = orgfs("compose", self.w.atoms)
        self.assertNotEqual(rc, 0)
        self.assertIn("pub/deeper/x.org", err)

    def test_story_org_cannot_be_an_atom(self):
        self.refuses(prose_atom("story.org", "x", "t\n"), "says story.org")

    def test_compose_refuses_an_out_file_inside_the_atoms(self):
        rc, out, err = orgfs("compose", self.w.atoms, "--out", os.path.join(self.w.atoms, "all.org"))
        self.assertNotEqual(rc, 0)
        self.assertFalse(os.path.exists(os.path.join(self.w.atoms, "all.org")))

    def test_compose_refuses_atoms_that_declare_nothing_valid(self):
        write(os.path.join(self.w.atoms, "pub", "member-h9.org"), member_atom("pub", "h1", "/x"))
        self.w.write_story()
        rc, out, err = orgfs("compose", self.w.atoms)
        self.assertNotEqual(rc, 0)
        self.assertIn("member-h9.org", err)

    def test_a_non_org_file_among_the_atoms_is_named_and_left_out(self):
        write(os.path.join(self.w.atoms, "NOTES.txt"), "hello\n")
        rc, out, err = orgfs("compose", self.w.atoms)
        self.assertEqual(rc, 0, err)
        self.assertIn("NOTES.txt", err)
        self.assertNotIn("hello", out)


class StoryTests(Fresh):
    """story.org: narrative Org text with an #+INCLUDE where each atom belongs."""

    def story(self, text):
        write(os.path.join(self.w.atoms, "story.org"), text)

    def test_an_atom_the_story_does_not_include_is_refused_by_name(self):
        write(os.path.join(self.w.atoms, "extra.org"), prose_atom("extra.org", "x", "t\n"))
        rc, out, err = orgfs("compose", self.w.atoms)
        self.assertNotEqual(rc, 0)
        self.assertIn("atom extra.org is not in story.org", err)

    def test_an_include_twice_or_of_a_non_atom_is_refused(self):
        s = read(os.path.join(self.w.atoms, "story.org")).decode()
        self.story(s + '\n#+INCLUDE: "preface.org"\n')
        rc, out, err = orgfs("compose", self.w.atoms)
        self.assertNotEqual(rc, 0)
        self.assertIn("story.org includes preface.org twice", err)
        self.story(s + '\n#+INCLUDE: "NOTES.org"\n')
        write(os.path.join(self.w.atoms, "NOTES.org"), "x\n")
        self.assertNotEqual(orgfs("compose", self.w.atoms)[0], 0)

    def test_without_a_story_the_atoms_follow_in_the_default_order(self):
        os.remove(os.path.join(self.w.atoms, "story.org"))
        rc, out, err = orgfs("compose", self.w.atoms)
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.encode(), read(self.w.composed))

    def test_narrative_between_the_includes_is_kept_and_the_atoms_sit_under_its_headings(self):
        w = self.w
        for s in ("priv", "sealed"):
            for f in list(atoms_of(w.atoms)):
                if f.startswith(s + "/"):
                    os.remove(os.path.join(w.atoms, f))
        members = SPOKES["pub"]["members"]
        for h in members:   # members are one level down in this story
            write(os.path.join(w.atoms, "pub", "member-%s.org" % h), member_atom("pub", h, w.roots[("pub", h)], level=2))
        self.story('#+INCLUDE: "preface.org"\n\n#+INCLUDE: "pub/spoke.org"\n\n* Who takes part\n\nA sentence of the story.\n\n'
                   + "\n".join('#+INCLUDE: "pub/member-%s.org"\n' % h for h in members) + "\n* The end\n\nLast words.\n")
        rc, out, err = orgfs("compose", w.atoms)
        self.assertEqual(rc, 0, err)
        self.assertIn("\n* Who takes part\n\nA sentence of the story.\n\n** member h1\n", out)
        self.assertTrue(out.index("** member h4") < out.index("* The end") < out.index("* Atoms and their hashes"))
        back = os.path.join(self._td.name, "back")
        comp = os.path.join(self._td.name, "narrative.org")
        self.assertEqual(orgfs("compose", w.atoms, "--out", comp)[0], 0)
        self.assertEqual(orgfs("decompose", comp, back)[0], 0)
        self.assertEqual(tree(back), tree(w.atoms))   # the narrative came back as story.org, byte for byte
        self.assertEqual(orgfs("check", comp, "--no-mounts")[1], orgfs("check", w.atoms, "--no-mounts")[1])

    def test_gap_lines_count_from_the_atom_s_own_heading_when_the_story_nests_it(self):
        w = self.w
        for s in ("priv", "sealed"):
            for f in list(atoms_of(w.atoms)):
                if f.startswith(s + "/"):
                    os.remove(os.path.join(w.atoms, f))
        for h in SPOKES["pub"]["members"]:
            write(os.path.join(w.atoms, "pub", "member-%s.org" % h), member_atom("pub", h, w.roots[("pub", h)], level=2))
        self.story('#+INCLUDE: "preface.org"\n\n#+INCLUDE: "pub/spoke.org"\n\n* Members\n\n'
                   + "\n".join('#+INCLUDE: "pub/member-%s.org"\n' % h for h in SPOKES["pub"]["members"]))
        view = orgfs("materialize", w.atoms, "--as", "out")[1]
        self.assertIn("pub/member-h1.org line 1", view)
        self.assertEqual(view, orgfs("materialize", self.w.atoms, "--as", "out")[1])

    def test_an_atom_must_already_have_the_level_the_story_gives_it(self):
        self.story('#+INCLUDE: "preface.org"\n\n* Under\n\n#+INCLUDE: "pub/spoke.org"\n')
        rc, out, err = orgfs("compose", self.w.atoms)
        self.assertNotEqual(rc, 0)
        self.assertIn("level 1", err)
        self.assertIn("level 2", err)
        self.assertIn("Org would change its stars", err)

    def test_text_straight_after_an_atom_is_refused_not_swallowed(self):
        s = read(os.path.join(self.w.atoms, "story.org")).decode()
        self.story(s.replace('#+INCLUDE: "preface.org"\n', '#+INCLUDE: "preface.org"\nA line that would be taken for the atom.\n'))
        rc, out, err = orgfs("compose", self.w.atoms)
        self.assertNotEqual(rc, 0)
        self.assertIn("preface.org", err)
        self.assertIn("heading of its own", err)

    def test_a_story_that_does_not_end_in_a_newline_is_refused(self):
        self.story(read(os.path.join(self.w.atoms, "story.org")).decode().rstrip("\n"))
        self.assertNotEqual(orgfs("compose", self.w.atoms)[0], 0)

    def test_the_story_is_part_of_the_manifest_and_a_change_to_it_is_stale(self):
        s = read(os.path.join(self.w.atoms, "story.org")).decode()
        self.story("\n" + s)
        rc, out, err = orgfs("check", self.w.composed, "--no-mounts")
        self.assertNotEqual(rc, 0)
        self.assertIn("STALE: story.org changed since compose", err)


class SourceFormTests(Fresh):
    def source(self, name="src.org", sub=None):
        d = self._td.name if sub is None else os.path.join(self._td.name, sub)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, name)
        rc, out, err = orgfs("compose", self.w.atoms, "--source", "--out", p)
        self.assertEqual(rc, 0, err)
        return p

    def test_the_source_form_is_the_story_with_includes_and_the_manifest(self):
        text = read(self.source()).decode()
        self.assertIn('#+INCLUDE: "w.d/preface.org"\n', text)
        self.assertIn('#+INCLUDE: "w.d/pub/member-h1.org"\n', text)
        self.assertNotIn(":ORGFS_ATOM:", text)
        self.assertEqual(manifest_rows(text), manifest_rows(read(self.w.composed).decode()))

    def test_the_include_paths_are_relative_to_where_the_file_is_written(self):
        p = self.source(sub="nested/deeper")
        self.assertIn('#+INCLUDE: "../../w.d/preface.org"\n', read(p).decode())
        self.assertEqual(orgfs("check", p, "--no-mounts")[0], 0)

    def test_to_stdout_the_paths_are_those_given_for_the_directory(self):
        rc, out, err = orgfs("compose", self.w.atoms, "--source", cwd=self._td.name)
        self.assertEqual(rc, 0, err)
        self.assertIn('#+INCLUDE: "%s/preface.org"\n' % self.w.atoms, out)

    def test_expanding_the_source_form_gives_the_composed_file_byte_for_byte(self):
        p = self.source()
        rc, out, err = orgfs("expand", p)
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.encode(), read(self.w.composed))

    def test_every_command_reads_the_source_form_with_the_same_identity(self):
        p = self.source()
        a = orgfs("check", p, "--no-mounts")
        self.assertEqual(a[0], 0, a[2])
        self.assertEqual(a[1], orgfs("check", self.w.atoms, "--no-mounts")[1])
        self.assertEqual(orgfs("plan", p, "--spoke", "pub"), orgfs("plan", self.w.atoms, "--spoke", "pub"))

    def test_a_changed_atom_makes_the_source_form_stale(self):
        p = self.source()
        a = os.path.join(self.w.atoms, "pub", "spoke.org")
        write(a, read(a).replace(b":STRIPES: 3", b":STRIPES: 4"))
        rc, out, err = orgfs("check", p, "--no-mounts")
        self.assertNotEqual(rc, 0)
        self.assertIn("STALE: pub/spoke.org changed since compose", err)

    def test_decompose_takes_the_source_form_too(self):
        p = self.source()
        back = os.path.join(self._td.name, "back")
        rc, out, err = orgfs("decompose", p, back)
        self.assertEqual(rc, 0, err)
        self.assertEqual(tree(back), tree(self.w.atoms))


class ExpandTests(unittest.TestCase):
    """The #+INCLUDE subset: what is expanded as Org does it, and what is refused."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.d = self._td.name

    def w(self, name, text):
        write(os.path.join(self.d, name), text)
        return os.path.join(self.d, name)

    def expand(self, name="top.org"):
        return orgfs("expand", os.path.join(self.d, name))

    def test_plain_include_relative_path_nesting_and_levels_agree_with_the_reference(self):
        self.w("a.org", "* A\n:PROPERTIES:\n:ID: a\n:END:\ntext of a\n\n** A1\nmore\n")
        self.w("sub/b.org", "\n\n** B\nb text\n#+INCLUDE: \"c.org\"\n\n\n")
        self.w("sub/c.org", "* C\nc text\n")
        self.w("top.org", 'lead-in\n#+INCLUDE: "a.org"\n\n* Top\nintro\n#+INCLUDE: "sub/b.org"\n** Sub\n#+INCLUDE: "a.org"\n')
        rc, out, err = self.expand()
        self.assertEqual(rc, 0, err)
        self.assertEqual(out, ref_expand(os.path.join(self.d, "top.org")))
        # worked by hand: under no heading A stays at 1; under "* Top" B goes to 2 and C (included from
        # inside B) to 3; under "** Sub" A goes to 3 and A1 to 4
        self.assertIn("\n* Top\nintro\n** B\nb text\n*** C\nc text\n** Sub\n*** A\n", out)
        self.assertIn("**** A1\n", out)

    def test_blank_lines_around_an_included_file_are_dropped_as_org_drops_them(self):
        self.w("a.org", "\n\n* A\ntext\n\n\n")
        self.w("top.org", 'x\n\n#+INCLUDE: "a.org"\n\ny\n')
        self.assertEqual(self.expand()[1], "x\n\n* A\ntext\n\ny\n")

    def test_an_empty_included_file_is_nothing(self):
        self.w("a.org", "\n \n")
        self.w("top.org", 'x\n#+INCLUDE: "a.org"\ny\n')
        self.assertEqual(self.expand()[1], "x\ny\n")

    def test_the_same_file_included_twice_one_after_the_other_is_fine(self):
        self.w("a.org", "* A\n")
        self.w("top.org", '#+INCLUDE: "a.org"\n#+INCLUDE: "a.org"\n')
        self.assertEqual(self.expand()[1], "* A\n* A\n")

    def test_the_refusals(self):
        self.w("a.org", "* A\n")
        self.w("notes.txt", "t\n")
        cases = [
            ('#+INCLUDE: "a.org" :lines "1-2"', ":lines"),
            ('#+INCLUDE: "a.org" :minlevel 2', ":minlevel"),
            ('#+INCLUDE: "a.org" src emacs-lisp', "arguments after the file name"),
            ('#+INCLUDE: "a.org" example', "arguments after the file name"),
            ('#+INCLUDE: "a.org" export html', "arguments after the file name"),
            ('#+INCLUDE: "a.org" :only-contents t', ":only-contents"),
            ('#+INCLUDE: "a.org::*A"', "URL or a search"),
            ('#+INCLUDE: a.org', "outside the supported subset"),
            ('#+INCLUDE: "/etc/passwd"', "absolute"),
            ('#+INCLUDE: "~/a.org"', "absolute"),
            ('#+INCLUDE: "https://example.org/a.org"', "URL"),
            ('#+INCLUDE: "notes.txt"', "only .org"),
            ('  #+INCLUDE: "a.org"', "indented"),
            ('#+include: "a.org"', "capitals"),
            ('#+INCLUDE: "missing.org"', "cannot read"),
            ('#+INCLUDE: "top.org"', "recursive"),
        ]
        for line, why in cases:
            self.w("top.org", "ok\n" + line + "\n")
            rc, out, err = self.expand()
            self.assertNotEqual(rc, 0, line)
            self.assertIn(why, err, line)
            self.assertIn("top.org:2", err, line)   # the message names the line

    def test_an_include_inside_a_block_or_a_drawer_or_a_comment_heading_is_refused(self):
        self.w("a.org", "* A\n")
        for text in ('#+BEGIN_SRC org\n#+INCLUDE: "a.org"\n#+END_SRC\n', ':LOGBOOK:\n#+INCLUDE: "a.org"\n:END:\n',
                     '* COMMENT hidden\n#+INCLUDE: "a.org"\n'):
            self.w("top.org", text)
            self.assertNotEqual(self.expand()[0], 0, text)

    def test_an_included_file_that_org_would_change_in_ways_we_do_not_reproduce_is_refused(self):
        for bad in ("see[fn:1]\n", "[[file:x]]\n", "[[../x]]\n", "#+BEGIN_SRC\n* a\n#+END_SRC\n", "* COMMENT c\n"):
            self.w("a.org", "* A\n" + bad)
            self.w("top.org", '#+INCLUDE: "a.org"\n')
            self.assertNotEqual(self.expand()[0], 0, bad)

    def test_an_included_file_without_a_final_newline_is_refused(self):
        self.w("a.org", "* A\nno newline")
        self.w("top.org", '#+INCLUDE: "a.org"\n')
        rc, out, err = self.expand()
        self.assertNotEqual(rc, 0)
        self.assertIn("newline", err)

    def test_a_text_that_org_would_not_have_made_is_not_decomposed(self):
        # a heading in the story over an atom at the same level: Org would have moved the atom
        self.w_ = None
        d = self.d
        write(os.path.join(d, "a.org"), prose_atom("a.org", "A", "t\n"))
        text = "* Heading of the story\n" + prose_atom("a.org", "A", "t\n")
        write(os.path.join(d, "x.org"), text)
        rc, out, err = orgfs("decompose", os.path.join(d, "x.org"), os.path.join(d, "out"))
        self.assertNotEqual(rc, 0)
        self.assertIn("Org would change its stars", err)
        self.assertFalse(os.path.exists(os.path.join(d, "out")))

    def test_recursion_through_two_files_is_refused(self):
        self.w("a.org", '#+INCLUDE: "b.org"\n')
        self.w("b.org", '#+INCLUDE: "a.org"\n')
        self.w("top.org", '#+INCLUDE: "a.org"\n')
        rc, out, err = self.expand()
        self.assertNotEqual(rc, 0)
        self.assertIn("recursive", err)


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
        hard = (b"* hard bytes\n:PROPERTIES:\n:ORGFS_ATOM: preface.org\n:END:\n\xc3\xa9\xff\x01 tab\t \n\n\n   \n\ttail line \n")
        write(os.path.join(self.w.atoms, "preface.org"), hard)
        self.w.compose()
        back = self.decompose_to()
        self.assertEqual(tree(back), tree(self.w.atoms))
        self.assertEqual(read(os.path.join(back, "preface.org")), hard)

    def test_decompose_then_compose_gives_the_composed_file_again(self):
        back = self.decompose_to()
        again = os.path.join(self._td.name, "again.org")
        self.assertEqual(orgfs("compose", back, "--out", again)[0], 0)
        self.assertEqual(read(again), read(self.w.composed))

    def test_decompose_splits_one_file_per_atom_and_never_merges(self):
        back = self.decompose_to()
        self.assertEqual(sorted(tree(back)), sorted(tree(self.w.atoms)))
        for p, data in atoms_of(back).items():
            self.assertEqual(data.count(b":ORGFS_ATOM:"), 1, p)
            self.assertEqual(data.count(b"* member ") + data.count(b"* spoke "), 0 if p == "preface.org" else 1, p)

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

    def test_text_outside_the_atoms_becomes_the_story_not_a_loss(self):
        p = os.path.join(self._td.name, "loose.org")
        write(p, b"Words of the story, before any heading.\n\n" + read(self.w.composed))
        out = os.path.join(self._td.name, "x")
        rc, o, err = orgfs("decompose", p, out)
        self.assertEqual(rc, 0, err)
        self.assertTrue(read(os.path.join(out, "story.org")).startswith(b"Words of the story, before any heading.\n\n#+INCLUDE: \"preface.org\"\n"))
        again = os.path.join(self._td.name, "again.org")
        self.assertEqual(orgfs("compose", out, "--out", again)[0], 0)
        cut = lambda b: b[:b.index(b"* Atoms and their hashes")]   # the manifest is new: the story gained a paragraph
        self.assertEqual(cut(read(again)), cut(read(p)))

    def test_decompose_refuses_a_path_that_climbs_out(self):
        p = os.path.join(self._td.name, "climb.org")
        write(p, prose_atom("../evil.org", "x", "t\n"))
        rc, out, err = orgfs("decompose", p, os.path.join(self._td.name, "x"))
        self.assertNotEqual(rc, 0)
        self.assertFalse(os.path.exists(os.path.join(self._td.name, "evil.org")))
        self.assertFalse(os.path.exists(os.path.join(self._td.name, "x")))

    def test_decompose_refuses_a_repeated_atom_a_bad_path_and_a_file_with_no_atoms(self):
        cases = {
            "twice": prose_atom("a.org", "a", "x\n") + prose_atom("a.org", "b", "y\n"),
            "badname": prose_atom("a b.org", "a", "x\n"),
            "deep": prose_atom("a/b/c.org", "a", "x\n"),
            "none": "* a heading\nno atom property anywhere\n",
        }
        for name, text in cases.items():
            p = os.path.join(self._td.name, name + ".org")
            write(p, text)
            rc, out, err = orgfs("decompose", p, os.path.join(self._td.name, "out-" + name))
            self.assertNotEqual(rc, 0, name)
            self.assertFalse(os.path.exists(os.path.join(self._td.name, "out-" + name)), name)

    def test_a_manifest_section_with_a_line_orgfs_did_not_write_is_refused(self):
        text = read(self.w.composed) + b"a stray remark\n"
        p = os.path.join(self._td.name, "stray.org")
        write(p, text)
        for args in (("check", p, "--no-mounts"), ("decompose", p, os.path.join(self._td.name, "o"))):
            rc, out, err = orgfs(*args)
            self.assertNotEqual(rc, 0, args)
            self.assertIn("not one orgfs wrote", err)


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

    def test_the_licensing_example_keeps_its_0_1_0_identity_in_every_form(self):
        atoms = os.path.join(T.ROOT_DIR, "examples", "licensing.d")
        comp = os.path.join(self._td.name, "licensing.org")
        src = os.path.join(self._td.name, "licensing-src.org")
        self.assertEqual(orgfs("compose", atoms, "--out", comp)[0], 0)
        self.assertEqual(orgfs("compose", atoms, "--source", "--out", src)[0], 0)
        for decl in (atoms, comp, src):
            out = orgfs("check", decl, "--no-mounts")[1]
            self.assertIn("identity\t1df8d78c7e2b291b459ee275c505504e655e75ee2e1693eeabe98e23c319e013", out)

    def test_prose_atoms_and_their_order_do_not_change_the_identity(self):
        base = orgfs("check", self.w.atoms, "--no-mounts")[1]
        write(os.path.join(self.w.atoms, "preface.org"), prose_atom("preface.org", "other", "Another paragraph.\n** A heading\nwords\n"))
        write(os.path.join(self.w.atoms, "zzz-more-prose.org"), prose_atom("zzz-more-prose.org", "more", "More.\n"))
        self.w.write_story()
        self.assertEqual(orgfs("check", self.w.atoms, "--no-mounts")[1], base)

    def test_spoke_blocks_in_a_composed_file_may_be_in_any_order(self):
        base = orgfs("check", self.w.composed, "--no-mounts")[1]
        atoms = atoms_of(self.w.atoms)
        body = lambda k: "".join("\n" + atoms[p].decode() for p in default_order([p for p in atoms if p.split("/")[0] == k or p == k]))
        shuffled = "".join(body(k) for k in ("sealed", "pub", "priv", "preface.org"))
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
        self.assertRegex(at, r"^priv/spoke\.org line 7 \(:VISIBILITY:\); withheld field declared at priv/member-h3\.org line 4 \(:ROOT:\)$")
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
                self.assertEqual(first, "* " + r["title"])

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
        write(os.path.join(self.w.atoms, "notes.org"), "* spoke loose\n:PROPERTIES:\n:ORGFS_ATOM: notes.org\n:RING: GRAIN\n:END:\n")
        s = read(os.path.join(self.w.atoms, "story.org")).decode()
        write(os.path.join(self.w.atoms, "story.org"), s + '\n#+INCLUDE: "notes.org"\n')
        self.refused("notes.org")

    def test_a_member_atom_with_no_member_in_it(self):
        write(os.path.join(self.w.atoms, "pub", "member-h1.org"), prose_atom("pub/member-h1.org", "nothing here", ""))
        self.refused("member-h1.org")

    def test_a_composed_file_with_a_heading_outside_its_atoms(self):
        p = os.path.join(self._td.name, "mix.org")
        text = read(self.w.composed)
        write(p, text[:text.index(b"* Atoms and their hashes")] + b"* spoke loose\n")   # no manifest: freshness is not what is tested
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
        write(os.path.join(self.w.atoms, "pub", "member-h9.org"), member_atom("pub", "h9", "/tmp/h9"))
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

    def test_the_story_text_of_a_composed_file_edited_in_place_is_refused_too(self):
        text = read(self.w.composed)
        write(self.w.composed, b"* A new first section\nwords\n\n" + text)
        os.rename(self.w.atoms, self.w.atoms + ".aside")
        rc, out, err = orgfs("check", self.w.composed, "--no-mounts")
        self.assertNotEqual(rc, 0)
        self.assertIn("STALE: story.org changed since compose", err)

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
        m = re.search(rb":ORGFS_IDENTITY: (\w{64})", text)
        write(dst, text.replace(m.group(1), b"0" * 64))
        rc, out, err = orgfs("check", dst, "--no-mounts")
        self.assertNotEqual(rc, 0)
        self.assertIn("STALE", err)

    def test_a_composed_file_without_a_manifest_is_read_and_says_so(self):
        p = os.path.join(self._td.name, "nomf.org")
        text = read(self.w.composed)
        write(p, text[:text.index(b"* Atoms and their hashes")].rstrip(b"\n") + b"\n")
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


class ExampleTests(unittest.TestCase):
    """The licensing example is the story a stranger reads: check that it keeps the promises it makes."""
    ATOMS = os.path.join(T.ROOT_DIR, "examples", "licensing.d")

    def test_the_example_composes_and_reads_top_to_bottom_as_a_story(self):
        rc, out, err = orgfs("compose", self.ATOMS)
        self.assertEqual(rc, 0, err)
        titles = [t for _, t in re.findall(r"(?m)^(\*+) (.*)$", out)]
        self.assertEqual(titles[0], "Why this spoke exists")
        order = ["Why this spoke exists", "spoke licensing", "Who takes part", "member gw12", "member gw34",
                 "What is shared, and what is kept back", "What is shared", "What is kept back, and why", "What to do next",
                 "If you are reading", "If you run a member", "Atoms and their hashes"]
        self.assertEqual([titles.index(t) for t in order], sorted(titles.index(t) for t in order))
        # every atom opens with a sentence of prose after its drawer, not with a bare drawer
        opens = re.findall(r"(?m)^:ORGFS_ATOM: (\S+)\n(?::[A-Z]+: .*\n)*:END:\n(.*)\n", out)
        self.assertEqual(len(opens), 10)
        for path, first in opens:
            self.assertRegex(first, r"^[A-Za-z0-9]", path)
        prose = re.sub(r"(?m)^(:.*|\|.*|\*.*|#\+.*)$", "", out)
        self.assertGreater(len(prose.split()), 350)
        self.assertNotRegex(out, r"(?m)^# ")

    def test_the_example_marks_what_is_a_fact_what_is_judged_and_what_is_an_example(self):
        rc, out, err = orgfs("compose", self.ATOMS)
        for word in ("Fact:", "Judged:", "Example, not a fact:", "Observed:"):
            self.assertIn(word, out)

    def test_gap_reasons_in_the_example_are_prose_not_codes(self):
        rc, out, err = orgfs("compose", self.ATOMS)
        kept = out[out.index("What is kept back, and why"):out.index("What to do next")]
        self.assertIn("not reachable from the machine that built the view", kept)
        self.assertNotRegex(kept, r"member-store|WHAT_KIND|GAP_ID|member-root")


class DemoTests(unittest.TestCase):
    def test_the_demo_composes_checks_both_forms_and_still_ends_well(self):
        rc, out, err = orgfs("demo")
        self.assertEqual(rc, 0, err)
        self.assertIn("same file identity from the atoms in world.d and from world.org: yes", out)
        self.assertIn("composing the atoms again gives the same bytes: yes", out)


if __name__ == "__main__":
    unittest.main()
