# Copyright 2026 Corwin Brust <corwin@bru.st> and Bryant Frankford (INPUTLOOP)
# SPDX-License-Identifier: GPL-3.0-or-later
PYTHON ?= python3
SHELLS ?= dash bash "busybox sh"
AWKS   ?= gawk mawk

.PHONY: check check-shells prove-red site demo compose source clean

check:
	sh -n bin/orgfs
	$(PYTHON) tests/run_all.py
	sh tests/org_check.sh

# every shell x every awk (slow; not part of `check`)
check-shells:
	@set -e; for sh in $(SHELLS); do for awk in $(AWKS); do \
	  echo "== shell: $$sh   awk: $$awk"; \
	  ORGFS_SHELL="$$sh" ORGFS_AWK="$$awk" $(PYTHON) tests/run_all.py 2>&1 | tail -6; \
	done; done

prove-red:
	$(PYTHON) tests/prove_red.py

# make site                  the example stack (read on another machine); this is what the tests use
# make site STACK=DIR ...    the stack in DIR, read by `orgfs stack DIR` on THIS machine during this build;
#                            then BUILD_RUN (STAMP-HASH12), BUILD_COMMIT (40 hex) and BUILD_LOG are named,
#                            and the page says it is live only if they and the atoms agree (README.org, CI)
STACK ?=
BUILD_RUN ?=
BUILD_COMMIT ?=
BUILD_LOG ?=
site:
	@if [ -n "$(STACK)" ]; then \
	  sh bin/orgfs site examples/licensing.d _site --stack "$(STACK)" --live --build-run "$(BUILD_RUN)" --build-commit "$(BUILD_COMMIT)" --build-log "$(BUILD_LOG)"; \
	else \
	  sh bin/orgfs site examples/licensing.d _site --stack examples/stack.d; \
	fi

# the composed file is generated from the atoms; it is not kept in git
compose:
	sh bin/orgfs compose examples/licensing.d --out examples/licensing.org

# the story with its #+INCLUDE lines, for Org to expand
source:
	sh bin/orgfs compose examples/licensing.d --source --out examples/licensing-source.org

demo:
	sh bin/orgfs demo

clean:
	rm -rf _site
