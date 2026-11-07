#!/usr/bin/env python3
# Copyright 2026 Corwin Brust <corwin@bru.st> and Contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Black-box tests for bin/orgfs.  Python 3 standard library only.

Environment:
  ORGFS_BIN    path to the tool under test (default: ../bin/orgfs)
  ORGFS_SHELL  shell that runs it (default: sh); may hold arguments
  ORGFS_AWK    awk the tool should use (default: awk)

The plan is also computed here, independently, from the spec in the README
(sha256 of spoke|stripe|host, the REPLICAS highest scores), so a fault in the
tool's hashing cannot hide behind itself.
"""
import hashlib
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(HERE)
BIN = os.environ.get("ORGFS_BIN", os.path.join(HERE, "..", "bin", "orgfs"))
SHELL = shlex.split(os.environ.get("ORGFS_SHELL", "sh"))


def orgfs(*args, env=None, cwd=None):
    e = dict(os.environ)
    e.setdefault("ORGFS_SHELL", "sh")
    if env:
        e.update(env)
    p = subprocess.run(SHELL + [BIN] + [str(a) for a in args], capture_output=True,
                       env=e, cwd=cwd, timeout=120)
    return p.returncode, p.stdout.decode(), p.stderr.decode()


def write_decl(path, members, name="t", ring="GREAT", stripes=5, replicas=2,
               vis="public", prose=""):
    lines = ["#+TITLE: test declaration", "", prose, "", "* spoke %s" % name,
             ":PROPERTIES:", ":RING: %s" % ring, ":STRIPES: %d" % stripes,
             ":REPLICAS: %d" % replicas, ":VISIBILITY: %s" % vis, ":END:", ""]
    for host, root, tr in members:
        lines += ["** member %s" % host, ":PROPERTIES:", ":ROOT: %s" % root,
                  ":TRANSPORT: %s" % tr, ":END:", ""]
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def ref_plan(spoke, hosts, stripes, replicas):
    out = {}
    for i in range(1, stripes + 1):
        scored = sorted(((hashlib.sha256(("%s|%d|%s" % (spoke, i, h)).encode()).hexdigest(), h)
                         for h in hosts), reverse=True)
        out[i] = sorted(h for _, h in scored[:replicas])
    return out


def parse_plan(text):
    plan = {}
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        s, hs = line.split("\t")
        plan[int(s)] = sorted(hs.split(","))
    return plan


def sha(b):
    return hashlib.sha256(b).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.d = self._td.name
        self.hosts = ["h%d" % i for i in range(1, 6)]
        self.roots = {}
        for h in self.hosts:
            self.roots[h] = os.path.join(self.d, h)
            os.makedirs(self.roots[h])
        self.decl = os.path.join(self.d, "t.org")
        self.write()

    def write(self, path=None, **kw):
        write_decl(path or self.decl, [(h, self.roots[h], "local") for h in self.hosts], **kw)

    def make_file(self, data, name="in.bin"):
        p = os.path.join(self.d, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def put(self, data):
        rc, out, err = orgfs("put", self.decl, self.make_file(data))
        self.assertEqual(rc, 0, err)
        oid = out.strip()
        self.assertEqual(oid, sha(data))
        return oid

    def slice_path(self, host, oid, i, n=5):
        return os.path.join(self.roots[host], ".orgfs", "t", oid, "%d.of.%d" % (i, n))

    def holders(self, i, stripes=5, replicas=2):
        return ref_plan("t", self.hosts, stripes, replicas)[i]


class CheckTests(Base):
    def test_check_prints_identity(self):
        rc, out, err = orgfs("check", self.decl)
        self.assertEqual(rc, 0, err)
        self.assertRegex(out, r"(?m)^identity\t[0-9a-f]{64}$")
        self.assertIn("check: OK", out)

    def test_identity_ignores_prose_order_and_case(self):
        _, a, _ = orgfs("check", self.decl)
        p2 = os.path.join(self.d, "p2.org")
        write_decl(p2, [(h, self.roots[h], "local") for h in self.hosts],
                   prose="Entirely different prose.\n* A heading about nothing\nMore words.")
        _, b, _ = orgfs("check", p2)
        p3 = os.path.join(self.d, "p3.org")
        write_decl(p3, [(h, self.roots[h], "local") for h in reversed(self.hosts)], ring="great")
        _, c, _ = orgfs("check", p3)
        self.assertEqual(a, b)
        self.assertEqual(a, c)

    def test_identity_changes_with_data(self):
        _, a, _ = orgfs("check", self.decl)
        for kw in ({"stripes": 6}, {"replicas": 3}, {"ring": "GRAND"}, {"vis": "private"}, {"name": "u"}):
            p = os.path.join(self.d, "x.org")
            write_decl(p, [(h, self.roots[h], "local") for h in self.hosts], **kw)
            rc, b, err = orgfs("check", p)
            self.assertEqual(rc, 0, err)
            self.assertNotEqual(a, b, kw)
        p = os.path.join(self.d, "x.org")
        write_decl(p, [(h, self.roots[h] + "x", "local") for h in self.hosts])
        os.makedirs(self.roots["h1"] + "x", exist_ok=True)
        _, b, _ = orgfs("check", p, "--no-mounts")
        self.assertNotEqual(a, b)

    def test_rejections(self):
        mem = [(h, self.roots[h], "local") for h in self.hosts]
        cases = {
            "ring": dict(ring="HUGE"), "vis": dict(vis="secret"),
            "stripes0": dict(stripes=0), "stripes999": dict(stripes=999),
            "replicas-too-many": dict(replicas=6), "replicas0": dict(replicas=0),
            "name": dict(name="Bad Name"),
        }
        for label, kw in cases.items():
            p = os.path.join(self.d, "bad.org")
            write_decl(p, mem, **kw)
            rc, _, err = orgfs("check", p)
            self.assertNotEqual(rc, 0, label)
            self.assertIn("orgfs:", err)
        p = os.path.join(self.d, "bad.org")
        write_decl(p, [("a", "relative/path", "local")] + mem[1:])
        self.assertNotEqual(orgfs("check", p)[0], 0)
        write_decl(p, [("a", self.roots["h1"], "nfs")] + mem[1:])
        self.assertNotEqual(orgfs("check", p)[0], 0)
        write_decl(p, mem + [mem[0]])
        self.assertNotEqual(orgfs("check", p)[0], 0)

    def test_rejects_unknown_property_and_incomplete_second_spoke(self):
        with open(self.decl) as f:
            text = f.read()
        p = os.path.join(self.d, "bad.org")
        with open(p, "w") as f:
            f.write(text.replace(":RING: GREAT", ":RING: GREAT\n:COLOUR: red"))
        self.assertNotEqual(orgfs("check", p)[0], 0)
        with open(p, "w") as f:    # several spokes are allowed now; an incomplete one is still refused
            f.write(text + "\n* spoke second\n:PROPERTIES:\n:RING: GRAIN\n:END:\n")
        self.assertNotEqual(orgfs("check", p)[0], 0)

    def test_missing_property_and_missing_file(self):
        with open(self.decl) as f:
            text = f.read()
        p = os.path.join(self.d, "bad.org")
        with open(p, "w") as f:
            f.write(text.replace(":REPLICAS: 2\n", ""))
        rc, _, err = orgfs("check", p)
        self.assertNotEqual(rc, 0)
        self.assertIn("REPLICAS", err)
        self.assertNotEqual(orgfs("check", os.path.join(self.d, "nope.org"))[0], 0)

    def test_sshfs_requires_mount_point(self):
        p = os.path.join(self.d, "s.org")
        mem = [(h, self.roots[h], "sshfs" if h == "h2" else "local") for h in self.hosts]
        write_decl(p, mem)
        mounts = os.path.join(self.d, "mounts")
        with open(mounts, "w") as f:
            f.write("tmpfs /dev/shm tmpfs rw 0 0\n")
        rc, _, err = orgfs("check", p, env={"ORGFS_MOUNTS": mounts})
        self.assertNotEqual(rc, 0)
        self.assertIn("mount point", err)
        with open(mounts, "w") as f:
            f.write("me@h2:/srv %s fuse.sshfs rw 0 0\n" % self.roots["h2"])
        rc, out, err = orgfs("check", p, env={"ORGFS_MOUNTS": mounts})
        self.assertEqual(rc, 0, err)
        rc, out, err = orgfs("check", p, "--no-mounts")
        self.assertEqual(rc, 0, err)

    def test_local_root_must_exist(self):
        p = os.path.join(self.d, "s.org")
        write_decl(p, [(h, self.roots[h] + ("/gone" if h == "h3" else ""), "local") for h in self.hosts])
        self.assertNotEqual(orgfs("check", p)[0], 0)


class PlanTests(Base):
    def test_matches_independent_reference(self):
        rc, out, err = orgfs("plan", self.decl)
        self.assertEqual(rc, 0, err)
        self.assertEqual(parse_plan(out), ref_plan("t", self.hosts, 5, 2))

    def test_same_every_run_and_every_cwd(self):
        outs = set()
        for cwd in (self.d, "/", self.roots["h2"]):
            for _ in range(2):
                outs.add(orgfs("plan", self.decl, cwd=cwd)[1])
        self.assertEqual(len(outs), 1)

    def test_independent_of_member_order(self):
        p = os.path.join(self.d, "r.org")
        write_decl(p, [(h, self.roots[h], "local") for h in reversed(self.hosts)])
        self.assertEqual(orgfs("plan", self.decl)[1].split("\n", 1)[1], orgfs("plan", p)[1].split("\n", 1)[1])

    def test_as_host_is_the_filtered_plan(self):
        full = parse_plan(orgfs("plan", self.decl)[1])
        for h in self.hosts:
            rc, out, err = orgfs("plan", self.decl, "--as", h)
            self.assertEqual(rc, 0, err)
            mine = parse_plan(out)
            self.assertEqual(mine, {s: hs for s, hs in full.items() if h in hs})
        self.assertNotEqual(orgfs("plan", self.decl, "--as", "nobody")[0], 0)

    def test_holders_distinct_and_counted(self):
        for s, hs in parse_plan(orgfs("plan", self.decl)[1]).items():
            self.assertEqual(len(hs), 2)
            self.assertEqual(len(set(hs)), 2)


class AgreeTests(Base):
    def test_same_agree(self):
        p = os.path.join(self.d, "copy.org")
        write_decl(p, [(h, self.roots[h], "local") for h in reversed(self.hosts)], prose="my copy")
        rc, out, err = orgfs("agree", self.decl, p)
        self.assertEqual(rc, 0, err)
        self.assertIn("agree: OK", out)

    def test_different_refused(self):
        p = os.path.join(self.d, "other.org")
        write_decl(p, [(h, self.roots[h], "local") for h in self.hosts], stripes=6)
        rc, out, err = orgfs("agree", self.decl, p)
        self.assertNotEqual(rc, 0)
        self.assertIn("DISAGREE", err)
        self.assertNotIn("agree: OK", out)

    def test_needs_two(self):
        self.assertNotEqual(orgfs("agree", self.decl)[0], 0)


class StoreTests(Base):
    def test_roundtrip_many_sizes(self):
        for n in (0, 1, 3, 5, 999, 25001):
            data = os.urandom(n)
            oid = self.put(data)
            out = os.path.join(self.d, "out%d" % n)
            rc, _, err = orgfs("get", self.decl, oid, out)
            self.assertEqual(rc, 0, "size %d: %s" % (n, err))
            with open(out, "rb") as f:
                self.assertEqual(f.read(), data, n)

    def test_layout_manifest_and_placement(self):
        data = os.urandom(10007)
        oid = self.put(data)
        plan = ref_plan("t", self.hosts, 5, 2)
        pieces = []
        with open(os.path.join(self.roots["h1"], ".orgfs", "t", oid, "MANIFEST")) as f:
            rows = [l.rstrip("\n").split("\t") for l in f]
        self.assertEqual(len(rows), 5)
        for i, (name, size, digest) in enumerate(rows, 1):
            self.assertEqual(name, "%d.of.5" % i)
            copies = [h for h in self.hosts if os.path.exists(self.slice_path(h, oid, i))]
            self.assertEqual(sorted(copies), plan[i])
            with open(self.slice_path(copies[0], oid, i), "rb") as f:
                b = f.read()
            self.assertEqual(int(size), len(b))
            self.assertEqual(digest, sha(b))
            pieces.append(b)
        self.assertEqual(b"".join(pieces), data)
        for h in self.hosts:
            with open(os.path.join(self.roots[h], ".orgfs", "t", oid, "MANIFEST")) as f:
                self.assertEqual([l.rstrip("\n").split("\t") for l in f], rows)

    def test_get_survives_a_lost_member(self):
        data = os.urandom(5000)
        oid = self.put(data)
        for h in self.hosts:
            saved = os.path.join(self.roots[h], ".orgfs")
            moved = saved + ".aside"
            os.rename(saved, moved)
            out = os.path.join(self.d, "o-" + h)
            rc, _, err = orgfs("get", self.decl, oid, out)
            self.assertEqual(rc, 0, "without %s: %s" % (h, err))
            with open(out, "rb") as f:
                self.assertEqual(f.read(), data)
            os.rename(moved, saved)

    def test_get_with_all_copies_of_a_slice_gone_fails(self):
        oid = self.put(os.urandom(5000))
        for h in self.holders(3):
            os.remove(self.slice_path(h, oid, 3))
        out = os.path.join(self.d, "out")
        rc, _, err = orgfs("get", self.decl, oid, out)
        self.assertNotEqual(rc, 0)
        self.assertIn("3.of.5", err)
        self.assertFalse(os.path.exists(out))

    def corrupt(self, path):
        with open(path, "rb") as f:
            b = bytearray(f.read())
        b[0] ^= 0xFF
        with open(path, "wb") as f:
            f.write(bytes(b))

    def test_get_refuses_when_every_copy_corrupt(self):
        oid = self.put(os.urandom(5000))
        for h in self.holders(2):
            self.corrupt(self.slice_path(h, oid, 2))
        out = os.path.join(self.d, "out")
        rc, _, err = orgfs("get", self.decl, oid, out)
        self.assertNotEqual(rc, 0)
        self.assertIn("CORRUPT slice 2.of.5", err)
        self.assertFalse(os.path.exists(out))

    def test_get_warns_and_uses_the_good_copy(self):
        data = os.urandom(5000)
        oid = self.put(data)
        hs = self.holders(4)
        self.corrupt(self.slice_path(hs[0], oid, 4))
        out = os.path.join(self.d, "out")
        rc, _, err = orgfs("get", self.decl, oid, out)
        self.assertEqual(rc, 0, err)
        self.assertIn("CORRUPT slice 4.of.5", err)
        with open(out, "rb") as f:
            self.assertEqual(f.read(), data)

    def test_get_refuses_consistently_forged_slice_and_manifest(self):
        oid = self.put(os.urandom(5000))
        forged = b"forged-bytes"
        for h in self.holders(1):
            with open(self.slice_path(h, oid, 1), "wb") as f:
                f.write(forged)
        for h in self.hosts:
            mp = os.path.join(self.roots[h], ".orgfs", "t", oid, "MANIFEST")
            with open(mp) as f:
                rows = [l.rstrip("\n").split("\t") for l in f]
            rows[0][1], rows[0][2] = str(len(forged)), sha(forged)
            with open(mp, "w") as f:
                f.write("".join("\t".join(r) + "\n" for r in rows))
        out = os.path.join(self.d, "out")
        rc, _, err = orgfs("get", self.decl, oid, out)
        self.assertNotEqual(rc, 0)
        self.assertIn("WHOLE-FILE", err)
        self.assertFalse(os.path.exists(out))

    def test_get_refuses_disagreeing_manifests(self):
        oid = self.put(os.urandom(5000))
        mp = os.path.join(self.roots["h4"], ".orgfs", "t", oid, "MANIFEST")
        with open(mp, "a") as f:
            f.write("extra\n")
        rc, _, err = orgfs("get", self.decl, oid, os.path.join(self.d, "out"))
        self.assertNotEqual(rc, 0)
        self.assertIn("manifest", err)

    def test_get_bad_id(self):
        for bad in ("zz", "A" * 64, "0" * 63):
            self.assertNotEqual(orgfs("get", self.decl, bad, os.path.join(self.d, "o"))[0], 0)
        rc, _, err = orgfs("get", self.decl, "0" * 64, os.path.join(self.d, "o"))
        self.assertNotEqual(rc, 0)

    def test_verify_ok_then_drop_then_restore(self):
        data = os.urandom(8000)
        oid = self.put(data)
        rc, out, err = orgfs("verify", self.decl)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("verify: OK", out)
        victim = self.holders(2)[0]
        path = self.slice_path(victim, oid, 2)
        with open(path, "rb") as f:
            saved = f.read()
        os.remove(path)
        rc, out, err = orgfs("verify", self.decl)
        self.assertNotEqual(rc, 0)
        self.assertIn("slice 2.of.5", out)
        self.assertIn("1 of 2", out)
        with open(path, "wb") as f:
            f.write(saved)
        self.assertEqual(orgfs("verify", self.decl)[0], 0)

    def test_verify_rejects_corrupt_and_truncated_copy(self):
        oid = self.put(os.urandom(8000))
        path = self.slice_path(self.holders(5)[1], oid, 5)
        with open(path, "rb") as f:
            saved = f.read()
        self.corrupt(path)
        rc, out, _ = orgfs("verify", self.decl)
        self.assertNotEqual(rc, 0)
        self.assertIn("corrupt", out)
        with open(path, "wb") as f:
            f.write(saved[:-1])
        rc, out, _ = orgfs("verify", self.decl)
        self.assertNotEqual(rc, 0)
        self.assertIn("corrupt", out)

    def test_verify_with_unreachable_member_fails(self):
        self.put(os.urandom(8000))
        os.rename(self.roots["h2"], self.roots["h2"] + ".gone")
        rc, out, err = orgfs("verify", self.decl)
        self.assertNotEqual(rc, 0)
        self.assertIn("down", out)

    def test_put_needs_all_members_and_refuses_sealed(self):
        f = self.make_file(b"abc")
        p = os.path.join(self.d, "sealed.org")
        write_decl(p, [(h, self.roots[h], "local") for h in self.hosts], vis="sealed")
        rc, _, err = orgfs("put", p, f)
        self.assertNotEqual(rc, 0)
        self.assertIn("sealed", err)
        self.assertFalse(os.path.exists(os.path.join(self.roots["h1"], ".orgfs")))
        os.rename(self.roots["h5"], self.roots["h5"] + ".gone")
        self.assertNotEqual(orgfs("put", self.decl, f)[0], 0)


class SiteTests(Base):
    def render(self, **kw):
        out = os.path.join(self.d, "site")
        if kw:
            self.write(**kw)
        rc, so, err = orgfs("site", self.decl, out)
        self.assertEqual(rc, 0, err)
        return out

    def pages(self, out):
        res = {}
        for n in ("index.html", "gaps.html", "licensing.html", "rights.html"):
            with open(os.path.join(out, n), encoding="utf-8") as f:
                res[n] = f.read()
        return res

    def test_files_and_semantics(self):
        out = self.render()
        for n in ("index.html", "gaps.html", "licensing.html", "rights.html", "orgfs.css", "licensing.tsv",
                  "licensing.txt", "view-visitor.org"):
            self.assertTrue(os.path.exists(os.path.join(out, n)), n)
        for n, html in self.pages(out).items():
            self.assertTrue(html.startswith("<!DOCTYPE html>"), n)
            self.assertIn('<html lang="en">', html)
            for tag in ("<title>", "<main", "<nav", "<header>", "<footer>", "<h1>", 'class="skip"'):
                self.assertIn(tag, html, (n, tag))
            self.assertEqual(html.count("<h1>"), 1, n)
        idx = self.pages(out)["index.html"]
        self.assertIn("<caption>", idx)
        self.assertIn('<th scope="col">', idx)
        self.assertIn(self.hosts[0], idx)
        self.assertEqual(idx.count("<tr><th scope=\"row\">") - len(self.hosts), 5)  # members + 5 stripes
        self.assertIn(orgfs("check", self.decl)[1].split("identity\t")[1][:64], idx)

    def test_no_script_no_style_attr_no_external_load(self):
        out = self.render()
        for n, html in self.pages(out).items():
            low = html.lower()
            self.assertNotIn("<script", low, n)
            self.assertNotIn("javascript:", low, n)
            self.assertIsNone(re.search(r"\sstyle\s*=", low), n)
            self.assertIsNone(re.search(r"\son[a-z]+\s*=", low), n)
            self.assertNotIn("<style", low, n)
            for m in re.finditer(r"<(link|img|iframe|embed|object|source|video|audio|script|form)\b[^>]*>", low):
                self.assertIsNone(re.search(r"(src|href|action|data)\s*=\s*[\"']?(https?:)?//", m.group(0)), (n, m.group(0)))
            links = re.findall(r"<link\b[^>]*>", low)
            self.assertEqual(len(links), 1, n)
            self.assertIn('href="orgfs.css"', links[0])
        with open(os.path.join(out, "orgfs.css")) as f:
            css = f.read().lower()
        self.assertNotIn("@import", css)
        self.assertNotIn("url(", css)
        self.assertNotIn("://", css)

    def test_rights_page_says_the_three_things(self):
        rights = self.pages(self.render())["rights.html"]
        text = re.sub(r"<[^>]+>", " ", rights)
        text = re.sub(r"\s+", " ", text)
        for phrase in ("keeps no access log", "sets no cookies", "loads nothing from another host"):
            self.assertIn(phrase, text)
        for claim in ("encrypt", "anonymi", "GDPR", "we never"):
            self.assertNotIn(claim.lower(), text.lower())

    def test_licensing_page_marks_unchecked(self):
        lic = self.pages(self.render())["licensing.html"]
        self.assertIn("unchecked", lic)
        self.assertIn("<caption>", lic)
        for name in ("dash", "nginx", "dehydrated", "OpenSSH", "Proxmox VE", "Debian", "git"):
            self.assertIn(name, lic)

    def test_private_hides_roots_and_sealed_is_only_a_name(self):
        out = self.render(vis="private")
        idx = self.pages(out)["index.html"]
        self.assertNotIn(self.d, idx)
        self.assertFalse(os.path.exists(os.path.join(out, "declaration.org")))
        out = self.render(vis="public")
        self.assertTrue(os.path.exists(os.path.join(out, "declaration.org")))
        # a sealed spoke is not refused any more: the site shows its name and a gap record, nothing else
        self.write(vis="sealed")
        out = os.path.join(self.d, "s2")
        rc, _, err = orgfs("site", self.decl, out)
        self.assertEqual(rc, 0, err)
        with open(os.path.join(out, "index.html")) as f:
            idx = f.read()
        self.assertIn("Spoke t: withheld", idx)
        for h in self.hosts:
            self.assertNotIn(self.roots[h], idx)
        self.assertFalse(os.path.exists(os.path.join(out, "declaration.org")))

    def test_html_escaping_of_roots(self):
        p = os.path.join(self.d, "e.org")
        weird = os.path.join(self.d, "a&b")
        os.makedirs(weird)
        write_decl(p, [("w1", weird, "local"), ("w2", self.roots["h2"], "local")], replicas=1, stripes=2)
        out = os.path.join(self.d, "se")
        rc, _, err = orgfs("site", p, out)
        self.assertEqual(rc, 0, err)
        with open(os.path.join(out, "index.html")) as f:
            idx = f.read()
        self.assertIn("a&amp;b", idx)
        self.assertNotIn("a&b", idx)


class DemoTests(unittest.TestCase):
    def test_demo_runs_and_reports(self):
        rc, out, err = orgfs("demo")
        self.assertEqual(rc, 0, out + err)
        for line in ("bytes identical to the originals: yes", "get still works with one member gone: yes",
                     "verify: OK (2 objects)", "== report"):
            self.assertIn(line, out)

    def test_usage_and_unknown(self):
        self.assertEqual(orgfs("--help")[0], 0)
        self.assertNotEqual(orgfs("frobnicate")[0], 0)
        self.assertRegex(orgfs("version")[1], r"^orgfs \d+\.\d+\.\d+")


if __name__ == "__main__":
    unittest.main(verbosity=1)
