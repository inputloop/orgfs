# Copyright 2026 Corwin Brust <corwin@bru.st> and Contributors
# SPDX-License-Identifier: GPL-3.0-or-later
PYTHON ?= python3
SHELLS ?= dash bash "busybox sh"
AWKS   ?= gawk mawk

.PHONY: check check-shells prove-red site demo compose clean

check:
	sh -n bin/orgfs
	$(PYTHON) tests/run_all.py

# every shell x every awk (slow; not part of `check`)
check-shells:
	@set -e; for sh in $(SHELLS); do for awk in $(AWKS); do \
	  echo "== shell: $$sh   awk: $$awk"; \
	  ORGFS_SHELL="$$sh" ORGFS_AWK="$$awk" $(PYTHON) tests/run_all.py 2>&1 | tail -6; \
	done; done

prove-red:
	$(PYTHON) tests/prove_red.py

site:
	sh bin/orgfs site examples/licensing.d _site

# the composed file is generated from the atoms; it is not kept in git
compose:
	sh bin/orgfs compose examples/licensing.d --out examples/licensing.org

demo:
	sh bin/orgfs demo

clean:
	rm -rf _site
