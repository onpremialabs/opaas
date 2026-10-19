# Releasing OPAAS

Every release is kept and never replaced ([D74](decisions.md#d74--a-release-is-kept-never-replaced)).
A `v*` tag cannot be moved or deleted once pushed, and a published release
cannot gain, lose or swap a file. A mistake is corrected with a new patch
version.

## Cutting a release

1. **Branch** `release/X.Y.Z` from `develop`.
2. **Bump the version in three places together.** These are `pyproject.toml`,
   `src/opaas/__init__.py` (`__version__`) and `CITATION.cff` (`version` and
   `date-released`). `OPAAS_SPEC_VERSION` changes only with the methodology.
3. **Turn `## [Unreleased]` in `CHANGELOG.md` into `## [X.Y.Z] — YYYY-MM-DD`.**
   That section becomes the release notes, word for word, so it is written for
   users, in English.
4. **Run what CI runs:**
   ```bash
   uv run pytest
   ```
   ```bash
   uv run ruff check src tests
   ```
   ```bash
   uv lock --check
   ```
5. **Open a pull request** from the release branch into `develop`, and merge it
   after review.
6. **Tag the merged release commit and push the tag.** Only administrators can
   create a `v*` tag:
   ```bash
   git tag -a vX.Y.Z -m "OPAAS X.Y.Z" <release-commit>
   ```
   ```bash
   git push origin vX.Y.Z
   ```
7. **The `Release` workflow does the rest.**
   - It checks that the tag, the package and the CHANGELOG agree.
   - It runs the full CI on the tag.
   - It builds the wheel and the sdist with pinned tools.
   - It creates the release from the CHANGELOG section, attaches the archives,
     `SHA256SUMS` and `BUILD-INFO.txt`, and publishes it.

   Do not create the release by hand: the workflow refuses a release that
   already has files.

## Titles and order

- **The title is `OPAAS X.Y.Z` and nothing else.** The workflow sets it. What
  the release contains belongs in the CHANGELOG section, which becomes the
  notes.
- **GitHub lists releases by tag date, newest first, not by version.** A tag
  cannot be moved or recreated, so its place in the list is permanent.
  Release in version order. A patch for an older line, such as 0.2.2 after
  0.3.0, will be listed above the newer versions. From now on that order is
  kept as it falls. The **Latest** label still goes to the highest version.
- **One exception was made, once:** `v0.2.1` was re-created on 2026-10-19,
  dated 2026-10-15, so that the existing releases read in version order
  (D74). It is not a precedent.

## If something went wrong

- **The workflow failed before publishing:** fix the cause on `develop`. Then
  release the fix as the next patch version. The tag that failed stays, and its
  release, if one was left as a draft, is deleted by hand.
- **A published release is wrong:** release the next patch version, and say in
  its CHANGELOG what was wrong with the previous one. Do not delete it, retag it
  or swap its files.

## Checking a release

Anyone can rebuild a tag and compare checksums:

```bash
git checkout vX.Y.Z
```

```bash
SOURCE_DATE_EPOCH=$(git log -1 --format=%ct HEAD) uv build --build-constraint .github/release/build-constraints.txt
```

```bash
sha256sum dist/*
```

Use the `uv` version and the constraints recorded in that release's
`BUILD-INFO.txt`. Build on Linux: on Windows a checkout may convert line endings
in files that `.gitattributes` does not pin, and the archives then differ.

## Backfilling an older tag

A tag released before the workflow existed gets its archives from a manual run
of `Release`, with the tag as input (Actions → Release → Run workflow). The run:

- uploads to the existing release and leaves its notes untouched;
- skips the tests, because the tag was tested when it was released;
- writes `BUILD-INFO.txt` saying the archives were built after the fact.

This works only while the release has no files, and only for releases published
before immutable releases were turned on.

## Repository settings this relies on

- **Tag ruleset** on `refs/tags/v*`:
  - block updates, force pushes and deletions, with no bypass;
  - restrict creation to repository administrators.
- **Immutable releases** turned on (Settings → General → Releases).
