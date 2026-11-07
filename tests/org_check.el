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
;;      named table are in the file, and no comment line of orgfs's own;
;;  (d) `orgfs who': Org parses the document, finds every [[#id]] link's target with its own
;;      `org-find-property', and every person, path and commit is linked to the other two kinds and
;;      linked back from them (the interlocked triangles); the counts agree with git's own;
;;  (e) `orgfs stack': Org expands the stack's source form to the bytes orgfs composes, reads each
;;      atom's heading tag (SEEN_HERE or CARRIED) and the gap records' properties, and a run that
;;      starts from a seed marks what it carried as CARRIED and nothing carried as SEEN_HERE.
;; No emacs: NOT CHECKED, never a pass (org_check.sh).  No git: (d) is NOT CHECKED and says so.

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

(defun oc-check-expanded (text dir label &optional no-identity allowed)
  "Org's reading of TEXT, the expanded form of the atoms in DIR.
NO-IDENTITY: the directory has no spokes, so `orgfs check' has no identity to print.
ALLOWED: the element types the file may hold (default `oc-allowed-elements')."
  (let (tree)
    (with-temp-buffer
      (insert text)
      (let ((org-inhibit-startup t)) (org-mode))
      (setq tree (org-element-parse-buffer))
      ;; (c) only Org syntax, nothing of ours
      (let ((bad (org-element-map tree org-element-all-elements
                   (lambda (e) (unless (memq (org-element-type e) (or allowed oc-allowed-elements)) (org-element-type e))))))
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
        (let ((ids (oc-property-values tree "ORGFS_IDENTITY")))
          (oc-assert (= (length ids) 1) "%s: %d ORGFS_IDENTITY properties" label (length ids))
          (unless no-identity
            (let ((printed (oc-orgfs-ok "check" dir "--no-mounts")))
              (oc-assert (string-match-p (concat "^file-identity\t" (regexp-quote (car ids)) "$") printed)
                         "%s: the identity on the manifest heading is not the one orgfs check prints" label))))))))

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


;;; (d) orgfs who

(defvar oc-who-allowed
  '(org-data headline section property-drawer node-property paragraph plain-list item example-block keyword)
  "The element types a `who' document holds.")

(defun oc-git-run (dir env &rest args)
  "Run git in DIR with ENV added to the environment; return its output."
  (let ((process-environment (append env '("GIT_CONFIG_GLOBAL=/dev/null" "GIT_CONFIG_NOSYSTEM=1" "GIT_TERMINAL_PROMPT=0")
                                     process-environment)))
    (with-temp-buffer
      (let ((rc (apply #'call-process "git" nil t nil "-C" dir args)))
        (oc-assert (eq rc 0) "git %S failed: %s" args (buffer-string))
        (buffer-string)))))

(defun oc-commit (dir name email date subject &optional files empty)
  (dolist (f files)
    (let ((p (expand-file-name (car f) dir)))
      (make-directory (file-name-directory p) t)
      (write-region (cdr f) nil p t 'silent)))
  (oc-git-run dir nil "add" "-A")
  (oc-git-run dir (list (concat "GIT_AUTHOR_NAME=" name) (concat "GIT_AUTHOR_EMAIL=" email) (concat "GIT_AUTHOR_DATE=" date)
                        "GIT_COMMITTER_NAME=Committer" "GIT_COMMITTER_EMAIL=c@example.org" (concat "GIT_COMMITTER_DATE=" date))
              "commit" "-q" "-m" subject (if empty "--allow-empty" "--no-edit")))

(defun oc-who-fixture ()
  (let ((repo (expand-file-name "who-repo" oc-tmp)))
    (make-directory repo t)
    (oc-git-run repo nil "init" "-q" "-b" "main")
    (oc-commit repo "Alice Example" "alice@example.org" "2026-01-10T09:00:00+00:00" "first" '(("a.txt" . "a\n") ("dir/b.txt" . "b\n")))
    (oc-commit repo "Bob Example" "bob@example.org" "2026-02-01T10:00:00+00:00" "second [[evil]] link" '(("a.txt" . "more\n") ("sp ace.txt" . "s\n")))
    (oc-commit repo "alice example" "alice@example.org" "2026-03-01T11:00:00+00:00" "the other spelling" '(("dir/b.txt" . "again\n")))
    (oc-commit repo "Alice Example" "alice@example.org" "2026-03-05T12:00:00+00:00" "empty" nil t)
    repo))

(defun oc-headline-id (hl)
  "The CUSTOM_ID of headline HL, from the first drawer of its section, or nil."
  (let ((sec (car (org-element-contents hl))))
    (when (eq (org-element-type sec) 'section)
      (let ((dr (car (org-element-contents sec))))
        (when (eq (org-element-type dr) 'property-drawer)
          (org-element-map dr 'node-property
            (lambda (np) (when (string= (upcase (org-element-property :key np)) "CUSTOM_ID") (org-element-property :value np)))
            nil t))))))

(defun oc-enclosing-headline (e)
  (let ((p (org-element-property :parent e)))
    (while (and p (not (eq (org-element-type p) 'headline))) (setq p (org-element-property :parent p)))
    p))

(defun oc-kind (id) (car (split-string id "-")))

(defun oc-check-who (doc repo label &optional narrowed)
  "Org's reading of DOC, what `orgfs who' wrote about REPO.  NARROWED: a range or paths were given, so git's whole-history counts do not apply."
  (with-temp-buffer
    (insert doc)
    (let ((org-inhibit-startup t)) (org-mode))
    (let* ((tree (org-element-parse-buffer))
           (bad (org-element-map tree org-element-all-elements
                  (lambda (e) (unless (memq (org-element-type e) oc-who-allowed) (org-element-type e)))))
           (ids (make-hash-table :test 'equal))
           (edges nil))
      (oc-assert (null bad) "%s: elements Org finds that a who document should not hold: %S" label (delete-dups (delq nil bad)))
      (org-element-map tree 'headline
        (lambda (hl) (let ((id (oc-headline-id hl)))
                       (when id
                         (oc-assert (not (gethash id ids)) "%s: duplicate CUSTOM_ID %s" label id)
                         (puthash id t ids)))))
      (org-element-map tree 'link
        (lambda (ln)
          (oc-assert (equal (org-element-property :type ln) "custom-id") "%s: a link of type %s" label (org-element-property :type ln))
          (let* ((to (org-element-property :path ln))
                 (hl (oc-enclosing-headline ln))
                 (from (and hl (oc-headline-id hl))))
            (oc-assert (gethash to ids) "%s: the link to %s resolves to no heading" label to)
            ;; and Org's own search finds it
            (oc-assert (save-excursion (org-find-property "CUSTOM_ID" to)) "%s: org-find-property does not find %s" label to)
            (when from (push (cons from to) edges)))))
      (setq edges (delete-dups edges))
      (dolist (e edges)
        (unless (equal (oc-kind (car e)) (oc-kind (cdr e)))
          (oc-assert (member (cons (cdr e) (car e)) edges) "%s: %s links %s but %s does not link back" label (car e) (cdr e) (cdr e))))
      (let ((commits 0) (people 0))
        (maphash
         (lambda (id _v)
           (let ((out (mapcar #'cdr (cl-remove-if-not (lambda (e) (equal (car e) id)) edges))))
             (pcase (oc-kind id)
               ("commit" (cl-incf commits)
                (oc-assert (= 1 (length (cl-remove-if-not (lambda (o) (equal (oc-kind o) "person")) out)))
                           "%s: commit %s does not link exactly one contributor" label id))
               ("person" (cl-incf people)
                (oc-assert (cl-find-if (lambda (o) (equal (oc-kind o) "commit")) out) "%s: %s links no commit" label id))
               ("path"
                (oc-assert (cl-find-if (lambda (o) (equal (oc-kind o) "commit")) out) "%s: %s links no commit" label id)
                (oc-assert (cl-find-if (lambda (o) (equal (oc-kind o) "person")) out) "%s: %s links no contributor" label id)))))
         ids)
        (unless narrowed
         (let ((want-commits (string-to-number (string-trim (oc-git-run repo nil "rev-list" "--count" "HEAD"))))
              (want-people (length (delete-dups (split-string (oc-git-run repo nil "log" "--no-mailmap" "--format=%an <%ae>") "\n" t)))))
          (oc-assert (= commits want-commits) "%s: %d commit headings, git counts %d" label commits want-commits)
          (oc-assert (= people want-people) "%s: %d contributor headings, git counts %d distinct name and email pairs" label people want-people))))
      (length edges))))

(defun oc-who-case ()
  (if (not (executable-find "git"))
      (oc-say "org_check: NOT CHECKED who (no git here)")
    (let* ((repo (oc-who-fixture))
           (doc (oc-orgfs-ok "who" repo))
           (n (oc-check-who doc repo "who")))
      (oc-assert (> n 10) "who: only %d links read" n)
      ;; a narrowed range reads the same way
      (oc-check-who (oc-orgfs-ok "who" repo "dir") repo "who dir" t)
      (oc-say "org_check: ok who (%d links resolved by Org; contributors, paths and commits linked both ways)" n))))

;;; (e) orgfs stack

(defun oc-stack-tags (tree)
  "An alist (ATOM-NAME TAGS ATOM-PATH CARRIED-FROM) for each top-level atom headline of TREE."
  (let (out)
    (org-element-map tree 'headline
      (lambda (hl)
        (when (= 1 (org-element-property :level hl))
          (let ((atom nil) (from nil) (sec (car (org-element-contents hl))))
            (when (eq (org-element-type (car (org-element-contents sec))) 'property-drawer)
              (org-element-map (car (org-element-contents sec)) 'node-property
                (lambda (np)
                  (let ((k (upcase (org-element-property :key np))))
                    (cond ((string= k "ORGFS_ATOM") (setq atom (org-element-property :value np)))
                          ((string= k "CARRIED_FROM") (setq from (org-element-property :value np))))))))
            (when (and atom (string-match-p "\\`tool-" atom))
              (push (list (org-element-property :raw-value hl) (org-element-property :tags hl) atom from) out))))))
    (nreverse out)))

(defun oc-stack-gaps-whole (tree label)
  "Every gap headline of TREE has the properties of a gap record and at least one ROUTE."
  (let ((n 0))
    (org-element-map tree 'headline
      (lambda (hl)
        (when (string-prefix-p "gap " (org-element-property :raw-value hl))
          (cl-incf n)
          (let (keys)
            (org-element-map (org-element-contents hl) 'node-property
              (lambda (np) (push (upcase (org-element-property :key np)) keys)))
            (dolist (k '("ROLE" "GAP_ID" "WHAT_KIND" "WHAT_SHAPE" "RULE" "RULE_AT" "HOW" "WHY" "REACH" "ROUTE"))
              (oc-assert (member k keys) "%s: gap %s lacks %s" label (org-element-property :raw-value hl) k))))))
    n))

(defun oc-stack-case ()
  (let* ((d1 (expand-file-name "stack1" oc-tmp))
         (d2 (expand-file-name "stack2" oc-tmp))
         (src (expand-file-name "stack-src.org" oc-tmp))
         (comp (expand-file-name "stack1.org" oc-tmp))
         (comp2 (expand-file-name "stack2.org" oc-tmp)))
    (oc-orgfs-ok "stack" d1)
    (oc-orgfs-ok "compose" d1 "--out" comp)
    (oc-orgfs-ok "compose" d1 "--source" "--out" src)
    (let ((org (oc-org-expand src)) (ours (oc-read comp)))
      (oc-assert (string= org ours) "(a) Org's expansion of the stack's source form differs from orgfs compose")
      (oc-check-expanded org d1 "stack" t (append oc-who-allowed '(quote-block horizontal-rule table table-row)))
      (let* ((tree (with-temp-buffer (insert org) (let ((org-inhibit-startup t)) (org-mode)) (org-element-parse-buffer)))
             (tags (oc-stack-tags tree)))
        (oc-assert tags "stack: Org finds no tool atoms")
        (dolist (a tags)
          (oc-assert (equal (nth 1 a) '("SEEN_HERE")) "stack: Org reads the tags of %s as %S, not (SEEN_HERE), with no seed" (car a) (nth 1 a))
          (oc-assert (null (nth 3 a)) "stack: %s says it was carried, with no seed" (car a)))
        (let ((ng (oc-stack-gaps-whole tree "stack")))
          (oc-assert (> ng 0) "stack: no gap record at all (the commits that make a version distinct is always one)"))))
    ;; the second run starts from the first
    (oc-orgfs-ok "stack" d2 "--seed" comp)
    (oc-orgfs-ok "compose" d2 "--out" comp2)
    (let* ((text (oc-read comp2))
           (tree (with-temp-buffer (insert text) (let ((org-inhibit-startup t)) (org-mode)) (org-element-parse-buffer)))
           (tags (oc-stack-tags tree)) (carried 0))
      (dolist (a tags)
        (let ((tg (nth 1 a)))
          (oc-assert (or (equal tg '("SEEN_HERE")) (equal tg '("CARRIED"))) "stack: %s has the tags %S" (car a) tg)
          (if (equal tg '("CARRIED"))
              (progn (cl-incf carried)
                     (oc-assert (equal (nth 3 a) "stack1.org") "stack: %s is CARRIED but names %S as its seed" (car a) (nth 3 a)))
            (oc-assert (null (nth 3 a)) "stack: %s is SEEN_HERE yet says it was carried" (car a)))))
      (oc-assert (> carried 0) "stack: nothing was carried although the seed was this very host")
      (oc-stack-gaps-whole tree "stack seeded")
      (oc-say "org_check: ok stack (%d atoms read by Org, %d carried from the seed, tags and gap records whole)" (length tags) carried))))

(defun oc-main ()
  (oc-assert (not (version< (org-release) "9.3")) "Org %s is older than 9.3: include levels differ; orgfs needs 9.3 or later" (org-release))
  (oc-assert (file-readable-p oc-bin) "no orgfs at %s" oc-bin)
  (oc-example-case)
  (oc-fixture-case)
  (oc-who-case)
  (oc-stack-case)
  (princ (format "org_check: OK (%d assertions; Org %s, Emacs %s)\n" oc-count (org-release) emacs-version)
         #'external-debugging-output)
  (ignore-errors (delete-directory oc-tmp t))
  (kill-emacs 0))

(oc-main)

;;; org_check.el ends here
