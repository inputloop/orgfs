#!/bin/sh
# Copyright 2026 Corwin Brust <corwin@bru.st> and Bryant Frankford (INPUTLOOP)
# SPDX-License-Identifier: GPL-3.0-or-later
# Run tests/org_check.el (Org itself as the second reader of orgfs files) in Emacs batch.
#
#   sh tests/org_check.sh
#
# Where there is no emacs this prints "org_check: NOT CHECKED (no emacs)" and exits 0, so a
# laptop without Emacs can still run `make check` -- but it is not a pass, and it says so.
# In CI (CI set) or with ORGFS_REQUIRE_EMACS=1, a missing emacs is a failure: Emacs is a
# QA requirement (Corwin, 2026-11-07 08:11).
here=$(cd "$(dirname "$0")" && pwd)
: "${ORGFS_BIN:=$here/../bin/orgfs}"
export ORGFS_BIN
EMACS=${EMACS:-emacs}
if ! command -v "$EMACS" >/dev/null 2>&1; then
  echo "org_check: NOT CHECKED (no emacs)"
  if [ -n "${CI:-}" ] || [ "${ORGFS_REQUIRE_EMACS:-}" = 1 ]; then
    echo "org_check: FAILED: emacs is required here (CI or ORGFS_REQUIRE_EMACS=1)" >&2
    exit 1
  fi
  exit 0
fi
exec "$EMACS" -Q --batch -l "$here/org_check.el"
