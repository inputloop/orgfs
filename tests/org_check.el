;;; org_check.el --- Emacs and Org as the second reader of orgfs  -*- lexical-binding: t; -*-

;; Copyright 2026 Corwin Brust <corwin@bru.st> and Contributors
;; SPDX-License-Identifier: GPL-3.0-or-later

;; orgfs says its files are Org: the story is Org text with #+INCLUDE lines, the file that
;; travels alone is "what Org's export makes of the story", each atom is a heading with an
;; :ORGFS_ATOM: property, the manifest is a named table.  The shell and the Python tests only
;; ever read those files with orgfs's own idea of Org.  This file asks Org.
;;
;;   emacs -Q --batch -l tests/org_check.el          (tests/org_check.sh does this, or says why not)
;;
;; ORGFS_BIN names the orgfs to test (default: bin/orgfs beside this tests directory).
;;
;; Written against Org 9.5.5 (Emacs 28.2, the CI container) and run here also on Org 9.6.
;; Only functions that are in 9.5.5 are used.  Guarded: `org-export-expand-include-keyword'
;; takes its default level for an include from the heading it sits under only since Org 9.3
;; (the :org-include-induced-level text property); before that an included file kept its stars.
;; orgfs depends on the newer behaviour, so an older Org is refused with a message, not run.
;;
;; The checks, for the example declaration and for a set of small fixtures:
;;  (a) Org's own expansion of the source form equals `orgfs compose' (or `orgfs expand'), byte for byte;
;;  (b) Org parses the expanded form: every :ORGFS_ATOM: is a property of the first drawer of a
;;      heading, they are exactly the atoms of the directory; the table named orgfs-manifest is
;;      found by org-element and read by org-table, its hashes agree with ones Emacs computes
;;      itself, and the identity on its heading is the one `orgfs check' prints;
;;  (c) no line is outside Org syntax: only headlines, property drawers, paragraphs, lists and the
;;      named table are in the file, and no comment line of orgfs's own.

;;; Code:

(require 'cl-lib)
(require 'subr-x)
(require 'org)
(require 'ox)
(require 'org-element)
(require 'org-table)

(defvar oc-count 0 "How many assertions have held.")
(defvar oc-here (file-name-directory (or load-file-name buffer-file-name default-directory)))
(defvar oc-repo (expand-file-name ".." oc-here))
(defvar oc-bin (or (getenv "ORGFS_BIN") (expand-file-name "bin/orgfs" oc-repo)))
(defvar oc-tmp (make-temp-file "orgfs-org-check" t))
(setq create-lockfiles nil)

(defun oc-fail (fmt &rest args)
  (princ (concat "org_check: FAILED: " (apply #'format fmt args) "\n") #'external-debugging-output)
  (ignore-errors (delete-directory oc-tmp t))
  (kill-emacs 1))

(defmacro oc-assert (cond fmt &rest args)
  `(if ,cond (cl-incf oc-count) (oc-fail ,fmt ,@args)))

(defun oc-say (fmt &rest args)
  (princ (concat (apply #'format fmt args) "\n") #'external-debugging-output))

(defun oc-read (file)
  "FILE as text, decoded as UTF-8 with no line-end conversion."
  (with-temp-buffer
    (let ((coding-system-for-read 'utf-8-unix)) (insert-file-contents file))
    (buffer-string)))

(defun oc-bytes (file)
  (with-temp-buffer
    (set-buffer-multibyte nil)
    (insert-file-contents-literally file)
    (buffer-string)))

(defun oc-write (file text)
  (make-directory (file-name-directory file) t)
  (let ((coding-system-for-write 'utf-8-unix)) (write-region text nil file nil 'silent)))

(defun oc-orgfs (&rest args)
  "Run orgfs with ARGS; return (STATUS STDOUT STDERR)."
  (let ((out (expand-file-name "stdout" oc-tmp)) (err (expand-file-name "stderr" oc-tmp)))
    (let ((rc (call-process "sh" nil nil nil "-c"
                            (concat "exec sh " (shell-quote-argument oc-bin) " "
                                    (mapconcat #'shell-quote-argument args " ")
                                    " > " (shell-quote-argument out) " 2> " (shell-quote-argument err)))))
      (list rc (oc-read out) (oc-read err)))))

(defun oc-orgfs-ok (&rest args)
  (let ((r (apply #'oc-orgfs args)))
    (oc-assert (eq (car r) 0) "orgfs %s exited %s: %s" (mapconcat #'identity args " ") (car r) (nth 2 r))
    (nth 1 r)))

(defun oc-org-expand (file)
  "FILE, a source form, as Org's own export expands its #+INCLUDE lines."
  (with-temp-buffer
    (let ((coding-system-for-read 'utf-8-unix)) (insert-file-contents file))
    (setq default-directory (file-name-directory (expand-file-name file)))
    (setq buffer-file-name (expand-file-name file))
    (let ((org-inhibit-startup t)) (org-mode))
    (org-export-expand-include-keyword)
    (set-buffer-modified-p nil)
    (buffer-string)))

;;; (b), (c): Org parses the expanded form

(defvar oc-allowed-elements
  '(org-data headline section property-drawer node-property paragraph plain-list item
    table table-row quote-block horizontal-rule)
  "The only kinds of element a composed file holds.")

(defun oc-parse-atoms (tree)
  "The :ORGFS_ATOM: values of TREE; each must be a property of the first drawer of a headline."
  (let (found)
    (org-element-map tree 'node-property
      (lambda (np)
        (when (string= (upcase (org-element-property :key np)) "ORGFS_ATOM")
          (let* ((drawer (org-element-property :parent np))
                 (section (org-element-property :parent drawer))
                 (hl (org-element-property :parent section)))
            (oc-assert (and (eq (org-element-type drawer) 'property-drawer)
                            (eq (org-element-type section) 'section)
                            (eq (org-element-type hl) 'headline)
                            (= (org-element-property :begin section) (org-element-property :contents-begin hl)))
                       ":ORGFS_ATOM: %s is not in the first property drawer of a headline" (org-element-property :value np))
            (push (org-element-property :value np) found)))))
    (nreverse found)))

(defun oc-property-values (tree key)
  (let (vals)
    (org-element-map tree 'node-property
      (lambda (np) (when (string= (upcase (org-element-property :key np)) key)
                     (push (org-element-property :value np) vals))))
    (nreverse vals)))

(defun oc-manifest-rows (tree)
  "The data rows of the table named orgfs-manifest, read by org-table: a list of (ATOM SHA256)."
  (let ((tbl (org-element-map tree 'table
               (lambda (e) (when (equal (org-element-property :name e) "orgfs-manifest") e)) nil t)))
    (oc-assert tbl "no table named orgfs-manifest")
    ;; :begin is the #+NAME line; org-table-to-lisp wants point on the table itself
    (goto-char (org-element-property :post-affiliated tbl))
    (let ((rows (cl-remove-if-not #'listp (org-table-to-lisp))))
      (oc-assert (equal (car rows) '("atom" "sha256")) "the manifest header is %S, not (atom sha256)" (car rows))
      (cdr rows))))

(defun oc-check-expanded (text dir label)
  "Org's reading of TEXT, the expanded form of the atoms in DIR."
  (let (tree)
    (with-temp-buffer
      (insert text)
      (let ((org-inhibit-startup t)) (org-mode))
      (setq tree (org-element-parse-buffer))
      ;; (c) only Org syntax, nothing of ours
      (let ((bad (org-element-map tree org-element-all-elements
                   (lambda (e) (unless (memq (org-element-type e) oc-allowed-elements) (org-element-type e))))))
        (oc-assert (null bad) "%s: elements Org finds that a composed file should not hold: %S" label (delete-dups (delq nil bad))))
      (oc-assert (not (string-match-p "^# " text)) "%s: a comment line" label)
      (oc-assert (not (string-match-p "orgfs-\\(atom\\|compose\\)" text)) "%s: a marker of the old kind" label)
      ;; (b) the atoms, found by Org
      (let* ((org-atoms (sort (oc-parse-atoms tree) #'string<))
             (files (sort (cl-remove-if (lambda (f) (string= f "story.org"))
                                        (mapcar (lambda (f) (file-relative-name f dir))
                                                (directory-files-recursively dir "\\.org\\'")))
                          #'string<)))
        (oc-assert (equal org-atoms files) "%s: Org finds the atoms %S, the directory holds %S" label org-atoms files)
        ;; the manifest table, and hashes Emacs computes itself
        (let* ((rows (oc-manifest-rows tree))
               (want (append '("story.org") files)))
          (oc-assert (equal (sort (mapcar #'car rows) #'string<) (sort (copy-sequence want) #'string<))
                     "%s: the manifest names %S, expected %S" label (mapcar #'car rows) want)
          (dolist (r rows)
            (oc-assert (string= (cadr r) (secure-hash 'sha256 (oc-bytes (expand-file-name (car r) dir))))
                       "%s: the manifest hash of %s is not the SHA-256 Emacs computes" label (car r))))
        ;; the identity, a property of the manifest heading
        (let ((ids (oc-property-values tree "ORGFS_IDENTITY"))
              (printed (oc-orgfs-ok "check" dir "--no-mounts")))
          (oc-assert (= (length ids) 1) "%s: %d ORGFS_IDENTITY properties" label (length ids))
          (oc-assert (string-match-p (concat "^file-identity\t" (regexp-quote (car ids)) "$") printed)
                     "%s: the identity on the manifest heading is not the one orgfs check prints" label))))))

;;; the cases

(defun oc-example-case ()
  (let* ((src-dir (expand-file-name "examples/licensing.d" oc-repo))
         (dir (expand-file-name "licensing.d" oc-tmp))
         (expanded (expand-file-name "licensing.org" oc-tmp))
         (source (expand-file-name "out/licensing-source.org" oc-tmp)))
    (copy-directory src-dir dir)
    (make-directory (file-name-directory source) t)
    (oc-orgfs-ok "compose" dir "--out" expanded)
    (oc-orgfs-ok "compose" dir "--source" "--out" source)
    (oc-assert (string-match-p "^#\\+INCLUDE: \"\\.\\./licensing\\.d/preface\\.org\"$" (oc-read source))
               "the source form does not include the atoms by a relative path")
    (let ((org (oc-org-expand source)) (ours (oc-read expanded)))
      ;; (a)
      (oc-assert (string= org ours) "(a) Org's expansion of the source form differs from orgfs compose (first difference at %s)"
                 (let ((i (compare-strings org nil nil ours nil nil))) (if (eq i t) "none" (abs i))))
      (oc-check-expanded org dir "example"))
    (oc-say "org_check: ok example (licensing.d: source form, expanded form, manifest)")))

(defvar oc-fixtures
  '(("top.org" . "lead-in\n#+INCLUDE: \"a.org\"\n\n* Top\nintro\n#+INCLUDE: \"sub/b.org\"\n** Sub\n#+INCLUDE: \"a.org\"\n#+INCLUDE: \"d.org\"\n#+INCLUDE: \"sub/up.org\"\n* Last\n#+INCLUDE: \"deep.org\"\nend\n")
    ("a.org" . "\n\n* A\n:PROPERTIES:\n:ID: a\n:END:\ntext of a\n\n** A1\nmore\n\n\n")
    ("sub/b.org" . "** B\nb text\n#+INCLUDE: \"c.org\"\n")
    ("sub/c.org" . "* C\nc text\n")
    ("d.org" . "\n  \n\t\n")
    ("sub/up.org" . "* Up\n#+INCLUDE: \"../e.org\"\n")
    ("e.org" . "*** E (a deep heading alone)\ne text\n")
    ("deep.org" . "**** Four\nunder Last\n"))
  "Small files for `orgfs expand' against Org: re-levelling, blank lines at the edges, nesting, `..'.")

(defun oc-fixture-case ()
  (dolist (f oc-fixtures)
    (oc-write (expand-file-name (car f) (expand-file-name "fx" oc-tmp)) (cdr f)))
  (let* ((top (expand-file-name "fx/top.org" oc-tmp))
         (org (oc-org-expand top))
         (ours (oc-orgfs-ok "expand" top)))
    (oc-assert (string= org ours) "(a) Org's expansion of the fixtures differs from orgfs expand:\n--- Org\n%s--- orgfs\n%s" org ours)
    (oc-assert (string-match-p "\n\\*\\*\\* A\n" ours) "the fixture did not exercise re-levelling")
    (oc-say "org_check: ok fixtures (re-levelling, blank edges, nesting, ..)")))

(defun oc-main ()
  (oc-assert (not (version< (org-release) "9.3")) "Org %s is older than 9.3: include levels differ; orgfs needs 9.3 or later" (org-release))
  (oc-assert (file-readable-p oc-bin) "no orgfs at %s" oc-bin)
  (oc-example-case)
  (oc-fixture-case)
  (princ (format "org_check: OK (%d assertions; Org %s, Emacs %s)\n" oc-count (org-release) emacs-version)
         #'external-debugging-output)
  (ignore-errors (delete-directory oc-tmp t))
  (kill-emacs 0))

(oc-main)

;;; org_check.el ends here
