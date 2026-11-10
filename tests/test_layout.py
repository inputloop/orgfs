#!/usr/bin/env python3
# Copyright 2026 Corwin Brust <corwin@bru.st> and Bryant Frankford (INPUTLOOP)
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for `orgfs layout`: each rule held, broken or not checked, on a fake tree.

The fake tree is built under a temporary --root.  Ownership is read as numbers
(ls -ldn) and resolved through the fake tree's own etc/passwd, so the tests do not
need to be root.  The "owned by root" clause can only be exercised when the tests
run as root; that is said in README.org, under NOT CHECKED.
"""
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_orgfs as T  # noqa: E402

orgfs = T.orgfs
STATES = {"held", "broken", "not checked"}


def parse(out):
    rules = {}
    for line in out.split("\n"):
        parts = line.split("\t")
        if len(parts) == 3:
            rules[parts[1]] = (parts[0], parts[2])
    return rules


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.r = os.path.join(self._td.name, "root")
        for d in ("c4", "spokes", "etc/c4"):
            os.makedirs(os.path.join(self.r, d))
        self.uid, self.gid = os.getuid(), os.getgid()
        # a fake root that is not us, so the "wrong owner" case is real even when run as root
        if self.uid == 0:
            self.wrong = (4242, 4242)
        else:
            self.wrong = None

    def passwd(self, c4_gid=None, host=None, host_gid=None):
        lines = ["root:x:0:0:root:/root:/bin/sh"]
        if c4_gid is not None:
            lines.append("c4:x:5001:%d::/c4:/bin/sh" % c4_gid)
        if host:
            lines.append("%s:x:5002:%d::/home/%s:/bin/sh" % (host, host_gid, host))
        with open(os.path.join(self.r, "etc", "passwd"), "w") as f:
            f.write("\n".join(lines) + "\n")

    def run_layout(self, *extra, host="gw34"):
        return orgfs("layout", "--root", self.r, *extra, env={"ORGFS_HOSTNAME": host})

    def test_every_rule_is_reported_in_one_of_three_states(self):
        self.passwd(c4_gid=self.gid)
        rc, out, err = self.run_layout()
        rules = parse(out)
        self.assertEqual(set(rules), {"c4-present", "spokes-present", "c4-owner", "etc-c4-owner", "spokes-roots"})
        for name, (state, detail) in rules.items():
            self.assertIn(state, STATES, name)
            self.assertTrue(detail, name)
        self.assertRegex(out, r"layout: \d+ held, \d+ broken, \d+ not checked")

    def test_a_good_tree_holds_via_the_c4_groups_ownership(self):
        if self.wrong:
            os.chown(os.path.join(self.r, "c4"), 0, 5001)
            os.chown(os.path.join(self.r, "etc/c4"), 0, 5001)
        self.passwd(c4_gid=self.gid if not self.wrong else 5001)
        rc, out, err = self.run_layout()
        rules = parse(out)
        self.assertEqual(rc, 0, out)
        self.assertEqual(rules["c4-present"][0], "held")
        self.assertEqual(rules["spokes-present"][0], "held")
        self.assertEqual(rules["c4-owner"][0], "held", out)
        self.assertEqual(rules["etc-c4-owner"][0], "held", out)
        self.assertEqual(rules["spokes-roots"][0], "not checked")

    def test_the_host_accounts_group_also_holds(self):
        self.passwd(c4_gid=9999, host="gw34", host_gid=self.gid if not self.wrong else 5002)
        if self.wrong:
            os.chown(os.path.join(self.r, "c4"), 0, 5002)
            os.chown(os.path.join(self.r, "etc/c4"), 0, 5002)
        rc, out, _ = self.run_layout(host="gw34.bru.st")
        rules = parse(out)
        self.assertEqual(rules["c4-owner"][0], "held", out)
        self.assertIn("gw34", rules["c4-owner"][1])

    def test_a_c4_owned_by_the_wrong_user_is_broken_and_fails(self):
        if self.wrong:
            for d in ("c4", "etc/c4"):
                os.chown(os.path.join(self.r, d), *self.wrong)
        self.passwd(c4_gid=self.gid + 1000, host="gw34", host_gid=self.gid + 2000)
        rc, out, err = self.run_layout()
        rules = parse(out)
        self.assertEqual(rules["c4-owner"][0], "broken", out)
        self.assertEqual(rules["etc-c4-owner"][0], "broken", out)
        self.assertEqual(rc, 1)
        self.assertIn("2 broken", out)

    def test_wrong_group_alone_is_broken_even_with_accounts_present(self):
        self.passwd(c4_gid=7777, host="gw34", host_gid=7778)
        if self.wrong:
            os.chown(os.path.join(self.r, "c4"), 5002, 5002)
        rc, out, _ = self.run_layout()
        self.assertEqual(parse(out)["c4-owner"][0], "broken")

    def test_missing_directories(self):
        os.rmdir(os.path.join(self.r, "etc/c4"))
        os.rmdir(os.path.join(self.r, "spokes"))
        os.rmdir(os.path.join(self.r, "c4"))
        self.passwd()
        rc, out, _ = self.run_layout()
        rules = parse(out)
        self.assertEqual(rules["c4-present"][0], "broken")
        self.assertEqual(rules["spokes-present"][0], "broken")
        self.assertEqual(rules["c4-owner"][0], "not checked")
        self.assertEqual(rules["etc-c4-owner"][0], "not checked")
        self.assertIn("applies where it exists", rules["etc-c4-owner"][1])
        self.assertEqual(rc, 1)

    def test_spokes_roots_rule_reads_the_declaration(self):
        self.passwd(c4_gid=self.gid)
        decl = os.path.join(self._td.name, "d.org")
        os.makedirs(os.path.join(self._td.name, "local"))
        T.write_decl(decl, [("gw12", os.path.join(self._td.name, "local"), "local"),
                            ("gw14", "/spokes/gw14/storage", "sshfs")], replicas=1, stripes=2)
        rc, out, err = self.run_layout("--decl", decl)
        self.assertEqual(parse(out)["spokes-roots"][0], "held", out + err)
        T.write_decl(decl, [("gw12", os.path.join(self._td.name, "local"), "local"),
                            ("gw14", "/mnt/elsewhere/gw14", "sshfs")], replicas=1, stripes=2)
        rc, out, err = self.run_layout("--decl", decl)
        self.assertEqual(parse(out)["spokes-roots"][0], "broken", out)
        self.assertIn("gw14", out)
        self.assertEqual(rc, 1)

    def test_a_container_with_its_own_identifier_passes(self):
        # a container we host may keep its own name ("orgfs"); the host-named account is the account of that name
        for name in ("orgfs", "Odd_name-7"):
            self.passwd(c4_gid=9999, host=name, host_gid=self.gid if not self.wrong else 5002)
            if self.wrong:
                os.chown(os.path.join(self.r, "c4"), 0, 5002)
                os.chown(os.path.join(self.r, "etc/c4"), 0, 5002)
            rc, out, err = self.run_layout(host=name)
            rules = parse(out)
            self.assertEqual(rc, 0, out)
            self.assertEqual(rules["c4-owner"][0], "held", out)
            self.assertIn(name, rules["c4-owner"][1])
            self.assertEqual([r for r, (st, _) in rules.items() if st == "broken"], [])
            self.assertIn("not a fault", out.split("\n")[0])

    def test_runs_on_the_real_host_without_crashing(self):
        rc, out, err = orgfs("layout")
        self.assertIn(rc, (0, 1), err)
        self.assertRegex(out, r"layout: \d+ held, \d+ broken, \d+ not checked")


if __name__ == "__main__":
    unittest.main(verbosity=1)
