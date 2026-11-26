# Releasing, with a DOI from Zenodo

A DOI makes a release citable and archived. Journals ask for one, and unlike a
plain web address it can go in a reference list. Zenodo is free, run by CERN,
and keeps a copy of the code whatever later happens to the repository.

GitHub and Zenodo are linked once. After that, publishing a release on GitHub
archives it and mints a DOI on its own.

## Before the first release

Check each of these, because the deposit is permanent and the files cannot be
swapped afterwards.

1. **Add your ORCID iD** to `CITATION.cff` (uncomment the `orcid:` line) and to
   `.zenodo.json`, inside the creator entry, as `"orcid": "0000-0002-...."`.
   Leave the field out rather than inventing a number.
2. **Check what is about to become public.** `git ls-files` lists exactly what
   goes in. Look for anything with a real password, a key, a database, or a
   photograph of a person. `.gitignore` already keeps out `.env`, `*.db`,
   `uploads/` and `models/`.
3. **Run the tests** and make sure all five end with "All checks passed":
   `test_app.py`, `test_demo.py`, `test_password_reset.py`,
   `test_plates_and_cells.py`, `test_concurrent.py`. Install `segno` first, or
   five barcode checks are skipped.
4. **Make the repository public**, if it is not already. Zenodo can only
   archive a release it can read.

## Linking GitHub to Zenodo, once

1. Sign in at https://zenodo.org with your GitHub account.
2. Open the profile menu and choose **GitHub**.
3. Press **Sync now**.
4. Find the repository in the list and switch its slider on.
5. Refresh the page and check it now appears as enabled.

## Each release

1. Update the version in `app.py` (`APP_VERSION`), `CITATION.cff` (`version`
   and `date-released`), `.zenodo.json` (`version`), the image tag in
   `deploy/docker-compose.yml`, the first line of `deploy/INSTALL_FOR_IT.md`,
   the version line in `README.md`, and add the CHANGELOG entry.
2. Commit and push. Wait for the tests to pass on GitHub Actions.
3. On GitHub choose **Releases**, then **Draft a new release**. Tag it
   `v4.7.2`, give it the same title, paste the CHANGELOG section as the
   description, and press **Publish release**.
4. Zenodo picks it up within a few minutes and gives two identifiers: one for
   this version, and a concept identifier that always points at the newest
   version.
5. Put the concept identifier in `CITATION.cff` under `identifiers`, in the
   README, and on the project page. In a paper, cite the **version**
   identifier of the release the paper describes, so a reader gets the code
   that was reviewed.
6. Leave that release alone afterwards. Later work becomes a new version with
   its own identifier under the same concept identifier.

## Uploading by hand instead

If the repository is to stay a project page and only the code should be
archived, skip the GitHub link and upload directly:

1. Build the zip from the committed files:
   `git archive --format=zip --prefix=microbial-digital-library/ -o MDL-4.7.2.zip HEAD`.
   Using `git archive` rather than zipping the folder keeps out anything
   untracked, such as a local database or `.env`.
2. At https://zenodo.org choose **Upload**, then **New upload**, and drop the
   zip in.
3. Fill in the form to match `.zenodo.json`: upload type **Software**, the
   title, yourself as creator with your ORCID, the description, the MIT
   licence, the keywords, and version 4.7.2.
4. Under **Related identifiers**, add the project page as *is documented by*.
5. Press **Publish**. The identifier appears at the top of the record.

A record can be deposited under **embargo**, with the identifier reserved and
the files released on a date you choose, or with **restricted access**, where
someone has to ask. Both are on the deposit form, under Access rights.

## One thing to watch

Zenodo reads both `CITATION.cff` and `.zenodo.json`, and if both are present it
uses `.zenodo.json` and ignores `CITATION.cff`. This repository has both, so
`.zenodo.json` is the one that decides what the Zenodo record says. Keep the two
saying the same thing, or the record and the citation file will drift apart.
