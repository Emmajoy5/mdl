# Changelog

## v4.7.2: first public release

Prepared for the public release and the archived deposit, 26 November 2026.

- **The optional assistant has been taken out.** Earlier versions could send a
  question and a summary of the catalogue to an outside provider if an
  administrator supplied a key. It was off unless that key was set, and it is
  now gone entirely, along with its setting and its dependency. Nothing in the
  app sends a strain, a photograph or a search anywhere. The November trial ran
  with the feature present but switched off, so what the testers used behaved
  exactly as this release does.

### Isolates are no longer lost when people save at the same moment

A lab runs one installation for everybody, so two people pressing Save in the
same second is normal. These fixes went in on 23 November 2026.

- **Two people saving at once were given the same collection ID, and one of them
  lost the isolate.** With six people adding the same species at the same moment,
  only one isolate was saved; the other five got the error page. The database's
  write lock is now taken before the next ID is worked out, so the second save
  waits, sees the first one's ID and takes the next. All six are saved. The same
  fix is applied to spreadsheet import.
- **A save during a long import gave up after five seconds** and the person lost
  what they typed. Saves now wait up to 30 seconds for their turn
  (`DB_BUSY_WAIT_MS`). Signing in writes too, so it waits the same way.
- **Reading and writing at the same time.** The database is switched to
  write-ahead logging on startup, which roughly doubled the work done per second
  in a test with several people saving and reading at once. The setting is kept in
  the database file, so it happens once. A database on a network share cannot use
  it, which is logged and otherwise ignored (the IT guide already says not to put
  it there).
- The error page no longer says "Nothing was lost". It now says that anything
  being saved was not saved, and to check the record before typing it again.
- **requirements-tested.txt**, the exact library versions the test suites last
  passed with, for installs that have to come up the same way every time.
  requirements.txt still asks for "this version or newer".
- `test_concurrent.py`, which runs six clients at the same moment and holds the
  database open under a save to check it waits.

### Changes from the first pilot feedback

Made after the first round of feedback from the pilot in our own lab
(6 responses, 11 to 12 November 2026). The version number is unchanged until
the pilot ends.

- **Cell shape and arrangement from the Gram stain.** The Gram stain form now asks
  for the cell shape and arrangement seen, next to the result and the photo, so a
  stain is recorded the way it is reported ("Gram-positive rods in chains"). The
  3D cell model follows the latest Gram stain that has a shape, and is rebuilt as
  soon as one is saved or voided. Both fields start as "Not recorded", so the
  app's own guess is never saved as an observation.
- Before any Gram stain is recorded, the shape is worked out from the morphology
  notes ("cocci in clusters"), then the species, the genus (98 genera of bacteria
  and yeasts) and the name, and only then the Gram result. A strain whose species
  was not in the built-in table used to be drawn as cocci, so a Bacillus came out
  round. The strain page says where the shape came from, and warns when it is
  only a guess.
- New shapes: coccobacillus, curved rod (vibrio) and spiral. New arrangements:
  clusters (staphylococci), packets of eight (sarcinae) and palisades, as well as
  single cells, pairs, chains, tetrads and budding.
- "Change the cell shape or arrangement" on the strain page corrects the model by
  hand. The model is rebuilt at once and the change goes in the strain's history.
  A correction lasts until the next Gram stain with a shape is recorded.
- **Plate size.** Every plate used to be treated as 90 mm. Uploads now ask for the
  size (35, 60, 90, 100 or 150 mm), starting from the size the lab used last. The
  colony sizes, every size rule in the photo analysis, and the 3D dish follow it.
  The illustrated plate uses the size of the strain's latest photo.
- **Writing, labels and glare.** Two new checks in the photo analysis: glare at
  the rim (greyer than the agar and in its outer band) and ink left beside pen
  strokes (dark and no more colourful than the agar) are no longer raised as
  colonies. Dark colonies away from writing are not affected.
- **Paint over what is not growth.** Each plate photo has "Fix the 3D for this
  photo": paint over pen marks, a label, print or glare with a finger or the
  mouse, and those areas stay flat in the 3D model and are left out of the
  coverage figure. The writing still shows on the photo texture. The plate size
  can be changed there too.
- **Gram stain photo.** The upload was hidden as "Micrograph (optional)" inside
  "Log a Gram stain". The strain page now has an "Add a Gram stain photo" button,
  the photo comes first in the form, a photo can be saved without a result, and
  each photo opens full size.
- Fixed: with two Gram stains on different dates, the strain's Gram result showed
  the older one. It now shows the latest.
- The pilot question "Did the app ever lose or wrongly change data?" now asks about
  lost or changed records, and says a 3D model that looks wrong is not lost data.
  One response in the first round answered "Yes, once" because of a cell shape.
- **Photo storage.** Large phone photos (plate and Gram stain) are turned upright,
  capped at 4096 pixels on the long side, and saved as JPEG quality 90 with full
  colour resolution and the colour profile kept; other metadata, including GPS
  location, is removed. A 12 megapixel photo takes about 40% of the space, and a 48 megapixel one about a tenth.
  Halving the colour resolution (the usual JPEG shortcut) was tried and rejected:
  it changed the colony coverage the analysis measured. Small, upright photos
  without a location are stored untouched, and a compact photo is never encoded
  twice. Admin, Photo storage can keep every photo exactly as uploaded instead.
  `python manage.py compact-photos [--apply]` shrinks photos already stored.
- The analysis now turns a photo the way its sideways note says, as browsers do,
  so painted areas and the background marker land where they were drawn.
- Finding the agar edge: a ring now counts only if it shows on most rays from the
  centre, so the lines of a counting grid are not taken for the edge and the glass
  wall is less often taken for it. On 12 versions of five test plates the edge was
  right 7 times, against 3 before. It is still sensitive on some plates, such as
  a counting grid photographed at another size; more work is needed there.
- Colony margins: notches up to 0.4 mm wide, left where a removed grid line or
  pen stroke crosses a round colony, are closed before the margin is judged, so
  round colonies on a counting grid are no longer read as irregular.
- `test_plates_and_cells.py` (81 checks).

## v4.7.1: Forgotten passwords

- **Forgot your password?** on the log-in page. A person enters their username or
  the email address on their account. If the account has an address and the server
  can send mail (`SMTP_HOST`, `SMTP_FROM`, `PUBLIC_BASE_URL`), a reset link valid for
  60 minutes is emailed, at most three an hour. Otherwise the lab's admins see the
  request under Admin > Password help, with a count in the menu. The page answers
  the same way whether or not the account exists.
- **Reset links from an admin.** "Make reset link" on the Admin page gives a
  single-use link, valid for 24 hours, to pass on in person. The person chooses their
  own password; the admin never knows it. "Reset password" is now "Set password",
  for when the person is there and cannot use a link.
- `python manage.py reset-link USERNAME` makes a link on the server, for a lab whose
  only admin is locked out.
- Links are stored only as SHA-256 hashes and work once; a new link cancels older
  ones. Using one changes the password, clears any lockout and ends every other
  sign-in to the account. Changing your own password, or an admin or
  `manage.py reset-password` setting one, now also ends the account's other sign-ins.
- My account has an optional email address, used only for reset links.
- Admin > Recent password resets lists every link and every password set by an
  admin or on the server; each step is also written to the audit log.
- `test_password_reset.py` (58 checks).

## v4.7.0: One lab per installation, safer records, better plate analysis

This release fixes a number of problems found in v4.6.0 and rebuilds the plate
image analysis, which has now been tried on five real plate photographs. Every
change below is covered by the test suite (591 checks in `test_app.py`, plus
`test_demo.py`). Both test files now work in a temporary folder, so they can be
run more than once and never touch a lab's real database.

### One lab per installation
- An installation now holds one lab, run by its own admins. The platform
  administrator account, activation codes and lab suspension are gone. A second
  lab runs its own installation with its own data folder.
- On the first start the app shows a setup page for the lab name and the first
  admin account. It asks for a one-time setup code printed in the terminal, so
  nobody else on the network can set up the lab first. On a server, `LAB_NAME`,
  `ADMIN_USERNAME` and `ADMIN_PASSWORD` do the same without the page.
- "Sign up" is now "Ask to join": it needs the lab's join code, and an admin
  approves the request. It can no longer create a lab.
- Messages from the Contact page go to the lab's admins, on the Admin page.
- New `manage.py` for the machine that hosts the app: list accounts, reset a
  password (which also clears a lockout), make someone an admin, and split an
  old database that held several labs. It replaces `reset_superadmin.py`.
- Upgrading from v4.0 to v4.6 removes the old platform account. A database with
  more than one lab is not opened until it has been split with `manage.py`.

### Revival tracking
- A failed revival check no longer restarts the clock. The strain stays red until
  a passing check is logged. Before, a failed check turned it green for a whole
  interval.
- A strain on a periodic schedule with no revival date used to show as "not due"
  indefinitely. It now counts from a new "Last revival before this app" date, or
  from the date stored, and is flagged red if neither is known.
- The due date is worked out from the log and counts from the latest passing
  check, so entering an older check never pulls it backwards. Future dates are
  refused.
- Periodic strains with no interval set are listed on the landing page.
- Entering an invalid result now shows a message on the form instead of an error
  page.

### Keeping the history
- Revival checks, Gram stains and plate photos are no longer deleted. A wrong
  entry is voided with a reason and stays on the strain page under "Voided
  entries". A 3D relief made from a voided photo is removed.
- A new audit log records strain edits (old and new values), photo edits, new
  entries, voided entries, removed accession numbers and deleted strains. It is
  shown as the strain's change history and included in the exports.
- Only an administrator can delete a strain, a reason is required, and a full
  copy of the record is saved to the audit log first.
- Each entry now records the account that was signed in, as well as the name
  typed on the form.
- When upgrading, existing revival dates are kept as the starting point, so no
  due date changes.

### Security and uploads
- "Build 3D model" now needs a form submission, so it is covered by the CSRF check.
- Every page sends a Content-Security-Policy.
- Uploaded photos are checked by their content, not their file name. TIFF and GIF
  files are stored as PNG so that every browser can show them.
- Labels printed on the host computer itself (`localhost`) get QR codes with the
  computer's address on the lab network, since a phone cannot open `localhost`.

### Plate image analysis
- The whole dish is kept in view (the photo was cropped before, which could cut
  off a dish near the edge). The app finds the dish and then the edge of the
  agar itself, so the lid and the glass wall are not mistaken for agar.
- Colour is measured on a fixed scale instead of being stretched for each photo.
  A blank plate used to come out as almost fully covered; it now reads "no
  growth".
- Each spot is compared with the agar around it, so uneven lighting, or a glove
  or bench seen through the agar, is not counted as growth.
- Surface texture is used as well as colour, which picks up translucent colonies
  and wrinkled growth that are close to the colour of the agar.
- Printed grid lines, marker writing, dot-matrix print and reflections at the rim
  are recognised and left out. Colonies touching a grid line or sitting on
  writing are kept.
- Results on the test photos: the counting-grid plate went from 2% to 10.6%
  coverage (the colonies had been removed along with the grid), the wrinkled lawn
  from 2.5% to 59%, and the plate with handwriting from 14.1% to 5.8%.
- "Elevation" has been renamed "density", because it only ever described how much
  of the plate was covered.
- The page now warns when the background behind the plate is uneven, says when
  the dish could not be found and how much was left out as writing or print, and
  notes that the figures have not been checked against manual scoring. The README
  has advice on photographing plates.
- Still to improve: small translucent colonies on a pale medium against a white
  bench are under-read (3.5% for a plate of roughly 13%), a plate held over a
  glove over-reads (28% for roughly 15%, with a warning shown), and a plate fully
  covered by growth needs a bare-agar point marked by hand.

### 3D models
- The downloadable STL and the 3D view now show the same plate. They used to be
  two different models.
- Colonies are drawn at typical sizes for each organism, about five times smaller
  than before, with one colony per site. The small cream specks, which looked
  like contamination, are gone.
- Cell models show how the cells are arranged and how big they are: tetrads for
  *Pediococcus*, budding yeast, rods in pairs or chains. The page gives the real
  size and the scale used.
- Bacterial cells are coloured by the strain's recorded Gram result.
- Reliefs built from photos are much finer, colonies are rounded rather than flat
  topped, writing stays flat, and the agar takes its colour from the photo. The
  page says the height is drawn, not measured.
- `generate_models.py` builds models the same way as the app.

### Exports, tools and running the app
- `export_catalog.py` leaves out feedback contact details, and cells that look
  like spreadsheet formulas are made safe before export.
- The count of print-ready strains was too high (one strain showed 8 instead of 2).
- `build_catalog.py` no longer adds example data to a live database.
- `python app.py` uses waitress when it is installed. `mdl_SSL_CERT` and
  `mdl_SSL_KEY` turn on HTTPS, which phones need to install the app, and
  `mdl_HOST` and `mdl_PORT` set the address. The README explains HTTPS on a lab
  network.

### Repository, citation and demo
- Added `CITATION.cff` and `.zenodo.json`, so each release can be cited with a DOI
  (see `docs/RELEASING.md`).
- The tests run on GitHub for Linux, Windows and macOS, and the Docker image is
  built and started on every change.
- Added issue and pull request templates, a contributing guide and a code of
  conduct.
- A public demo mode (`mdl_DEMO=1`) sets up one demo lab with example strains,
  real plate photos and 3D models, behind a shared account. Account and user
  settings are switched off and everything is reset every few hours.
  `deploy/huggingface/` explains how to host it for free.

### Example strains
- *Lactobacillus brevis* is now *Levilactobacillus brevis* (Zheng et al., 2020),
  ID mdl-LEVBRE-004.
- *Acetobacter* is now revived every 60 days, since acetic acid bacteria lose
  viability in storage.

## v4.6.0: A fuller 3D view of the plate

- The 3D viewer now shows a plate rather than a flat disc: a glass Petri dish, a glossy
  agar bed, and colonies drawn the way the strain grows. Smooth or slimy strains appear
  as glossy domes with a few fused satellites, granular strains as heaps of small beads,
  and rhizoid or filamentous strains as branching chains of beads. Small satellite
  colonies are scattered on the agar. The plate now lies flat in the viewer; before, it
  stood on its edge. Colours are illustrative, as before.
- Models built from a plate photo sit inside the same glass dish, with the photo as the
  colour of the surface.
- Cell models are glossier and stand upright.
- A "White background" / "Dark background" button under each viewer switches the
  backdrop (remembered in that browser only).
- The downloadable STL files are unchanged: still a single watertight, printable relief.
- Models made before this version keep their old look until you press "Generate 3D model"
  again (or "Build 3D model" for a photo).
- New file `scene3d.py`. The colony texture PNG is no longer written, since the viewer no
  longer uses it.

## v4.5.2: Light theme and a new landing page

- The whole app now uses a white, light theme (page, cards, forms, calendar picker,
  badges, flash messages), with a deeper teal for links and buttons so text stays easy
  to read. The 3D model viewer keeps a dark background with a soft spotlight, so colony
  and cell models stand out against the white page.
- New landing page: a hero panel with the logo, a one-line summary and two main buttons;
  a statistics strip; the revival table (or an "All clear" note); line-icon action cards
  replacing the emoji tiles, now including a link to the storage map; and a tidier
  "Recently added" list.

## v4.5.1: Clear statements about where data lives

- Footer, About page, Sign up page, Contact page and README now say plainly that
  data is stored on the computer or server that runs the installation. If that is a
  rented or cloud server, the data is in the care of its provider.
- They also say that the authors never receive the data, that the person managing
  the computer or server can open the files directly (including every lab's data on a
  shared installation), and that Contact messages go to the installation's platform
  administrator, not to the authors.
- The footer no longer says the data "stays on your lab's own network", which was not
  true for a server outside the lab.

## v4.5.0: Evaluation questionnaire for research

- The feedback form is now a full evaluation questionnaire. Only the overall
  rating and ease of use are required. Everything else is optional.
  - A. About you and your lab: role, experience, lab type, collection size, team
    size, how long and how often it is used, devices.
  - B. Before and after: how cultures were tracked before, minutes to find and to
    record a strain before and now, hours saved per week.
  - C. What the app caught: mix-ups, overdue cultures, lost strains found, speed,
    errors seen, any data problem and what happened.
  - D. Usefulness of twelve features, with "did not use".
  - E. The ten item System Usability Scale, scored 0 to 100 automatically.
  - F. Likelihood to recommend, plans to keep using it, install or hosted
    preference, best and most frustrating things.
  - Consent to research use, and optional name and email for a follow up.
- When a response is sent, the app records measured usage: strains, strains with a
  location, plate photos, revival checks, days in use and app version.
- Answers are checked. A wrong or out of range answer keeps everything typed and
  explains what to fix.
- The summary page shows the usability score, recommend score, before and after
  averages, feature ratings, the numbers people reported, who answered, and
  written comments.
- Lab admins can download a research ZIP: an anonymous `responses.csv` (only
  consenting responses, or all with a button) and a `data_dictionary.csv`. Names
  and emails are never included, and text that looks like a spreadsheet formula is
  neutralised.
- The questionnaire lives in a new file, `feedback_survey.py`.
- Older feedback keeps working.

## v4.4.4: Clear responsibility and no liability notices

- README: new "Before you rely on it" section. It says that whoever runs an
  installation is responsible for its data, that the authors have no access and
  are not liable for data loss, leaks or wrong records, and what to do about
  backups, passwords, updates and regulated work.
- New `SECURITY.md` with a private way to report a security problem.
- About page: new "Responsibility for the data" card with the same points.

## v4.4.3: Ready to share

- Added an MIT `LICENSE` file, a `.gitignore` that keeps databases, uploads, models,
  `.env` and the secret key out of a public repository, and `.gitkeep` files so the
  upload and model folders exist after a download.
- README: new "How people open it" section (this computer, the same network, or a
  server), plus Licence, How to cite and Feedback sections.
- README: corrected the opening paragraph, which said 3D models come back
  automatically. A 3D model is built from a plate photo when you ask for one.
- README: removed " -- " dash signs from the text.

## v4.4.2: Storage location for every new isolate

- Adding an isolate now requires a freezer or fridge and a box. Rack and position
  are optional but encouraged.
- The Add form also asks for storage format, storage temperature, date stored and
  other location notes, so they no longer have to be filled in later on the Edit page.
- Saving an isolate (new or edited) at a position another isolate already holds
  shows a warning that names the other isolate. The save still goes through.
  Only your own lab's records are compared.
- The Storage page shows a notice at the top with the number of isolates that have
  no location, linking to the list.
- Editing an isolate with the full name left blank keeps the existing name instead
  of failing.
- Existing isolates without a location are not affected. The Edit page still
  allows them to be filled in at any time.

## v4.4.1: Plain wording, no dash signs

- Rewrote text across the app in plain language. No " -- ", em dashes or en dashes
  appear in the visible text of any page.
- Empty values now show "n/a" instead of a dash.
- Corrected the home page: it no longer says 3D models or colony counts happen
  automatically. A 3D model is built from a plate photo when you ask for one, and
  colony counts are entered by a person.
- Sign up, Import, Upload, Labels, Connect, Contact and About wording simplified.
- New test checks the visible text of the main pages for dash signs and fails if
  any page cannot be opened.

## v4.4.0: About and Contact pages

- **About** (`/about`, open to everyone, linked in the menu and footer): what the
  tool does, how data is handled, what it does not do (including that colony
  counts are entered by a person), who can do what, and technical details with
  the version number.
- **Contact** (`/contact`, open to everyone): a message form (question, access
  request for an activation or join code, problem, feature request, other).
  Messages go to the platform administrator's new **Messages** section on the
  Platform admin page, where they can be marked read or deleted. A signed-in
  person's lab and username are recorded from their session, not from the form.
  Basic spam protection: length limits, an address check, a hidden trap field
  and a per-address rate limit.
- **Who to contact** is set in `.env` (`CONTACT_NAME`, `CONTACT_ORG`,
  `CONTACT_EMAIL`, `CONTACT_PHONE`, `CONTACT_WEBSITE`, `CONTACT_NOTE`). Only what
  you set is shown, nothing is shown by default, and website links must be
  http(s).
- Also fixed: dropdown placeholders such as "-- select --" are now "Select".

## v4.3.4: Wording: no more " -- " in the app's text

- Every visible message, page text, heading, window title, form hint and
  console message that used a double hyphen (" -- ") as a dash now uses normal
  punctuation (full stops, commas, colons, brackets, or " | " in page titles).
  Wording is otherwise unchanged. Code comments and the README are untouched.

## v4.3.3: Controlled access: join codes with admin approval, activation codes for new labs

(Replaces the v4.3.2 package, which had join codes but no approval step and no
activation codes for new labs.)

- **The problem:** signing up on a second device created a second, empty lab,
  so data entered there did not appear in the first lab. That is how sign-up
  was designed (every sign-up made a new lab, with no approval), but nothing
  told you, there was no way to join an existing lab, and anyone who could
  reach the install could create a lab.
- **Joining an existing lab:** each lab has a **join code** (Admin > Join
  code). Entering it on the sign-up page ("Joining a lab that already
  exists?") sends a request. The person's account stays **inactive until a lab
  admin approves it** (Admin > Join requests; a count badge shows in the menu;
  Approve or Decline). Until then they can't see anything, and logging in says
  they are waiting for approval. Approved people are regular users. "Make a new
  code" invalidates the old one; codes for suspended labs don't work. A leaked
  code on its own gets nobody in.
- **Creating a new lab (becoming a lab admin) needs an activation code from
  the platform's primary administrator.** The superadmin's Platform admin page
  creates single-use codes (optional note, valid 14 days, can be revoked, and
  shows which lab used each). The sign-up form for a new lab requires one.
  Existing labs are unaffected. Set `REQUIRE_ACTIVATION_CODE=0` in `.env` to
  turn this off for a private, single-user install.
- **Lost superadmin password:** `python reset_superadmin.py` (run on the
  machine hosting the app) sets a new one. Labs and data are untouched.
- Sign-up refuses to create a lab with the same name as an existing one
  (ignoring case and spaces); renaming a lab to another lab's name is refused
  too. Existing installs get a join code for every lab on first start.
- Labs that were already duplicated by mistake are not merged automatically.
  To move data across: sign in as the stray lab's admin, download the ZIP
  export and unzip it, then sign in to the lab you want to keep and import the
  `catalog_flat.csv` file from it (photos and 3D models are not carried over).

## v4.3.1: Fix: 3D viewers blank on Windows

- **Fixed:** on Windows every 3D viewer on a strain page showed an empty dark
  box. Model paths were saved with backslashes (`models\t1-...glb`), which the
  v4.3 file-ownership check and the page template could not read. Paths are now
  stored with forward slashes, the checks accept either form, and **existing
  databases repair themselves on the next start** (restart the app once).
- The 3D viewer script is now bundled in `static/vendor/` instead of loaded
  from the internet, so it works offline and behind firewalls.
- If a viewer cannot load or a model cannot be displayed, the page now says
  so instead of showing a blank box.
- Models open at a closer default camera so they fill the viewer.
- Regression test added for Windows-style stored paths.

## v4.3: Hardened for real use; bulk import; custom fields; structured storage; printable labels

**Security and robustness**
- CSRF protection on every POST (session token in a hidden `csrf_field()`;
  a bad or missing token returns a 400 page instead of acting).
- Brute-force protection on sign-in: an account locks for 15 minutes after
  6 wrong passwords (persisted, so a restart doesn't reset it), plus a
  per-IP limit of 20 sign-in/sign-up attempts per 5 minutes. An admin
  resetting a password clears the lock. The IP limiter is in memory, so run
  one gunicorn worker (documented in the README). `X-Forwarded-For` is only
  trusted with `TRUST_PROXY_HEADERS=1`.
- `FORCE_HTTPS_COOKIES=1` marks the session cookie Secure for use behind an
  HTTPS proxy. `APP_DATA_DIR` moves the database, photos, models and session
  key into one folder.
- Deployment: `wsgi.py` (gunicorn entry point), `Dockerfile` + `.dockerignore`
  with a `/data` volume. **The gunicorn path was run and checked; the
  Dockerfile has not been built or run** (no Docker daemon was available when
  it was written), so treat it as a starting point.
- `backup.py`: one-command, consistent snapshot of the database (SQLite's
  online backup API, safe while the app runs) plus photos, models and the
  session key, with `--keep N` pruning. Restore steps are in the README.

**Bulk import (admin)** -- Admin > Import strains from a file
- Upload a .csv or .xlsx strain list (up to 5,000 rows). Your own column
  names are matched automatically and can be corrected on a preview page
  before anything is saved; a template CSV is provided.
- Handles an "Organism" column holding genus + species + strain, accession
  lists ("NCYC 505; ATCC 18824"), tags, Gram stain wording, day-first dates,
  revival interval/last revival, source labs (created if new), structured
  storage and custom-field columns. Excel files saved as Windows-1252 CSV
  read correctly.
- Rows are validated and reported per row (errors skip the row, warnings
  import it with the bad cell left blank). Likely duplicates -- an accession
  already in your catalog or repeated in the file, or the same
  genus/species/strain -- are flagged and unticked. The import is one
  transaction: if anything fails, nothing is added. Staged uploads are
  private to the lab that made them and deleted after import (or after 24h).
- Imported strains get collection IDs exactly as hand-entered ones do.
- Row numbers in the preview match the spreadsheet's own row numbers.

**Custom fields (per lab)** -- Admin > Custom fields
- Each lab can define up to 30 extra fields (text, number, date, or pick
  from a list). They appear on every strain's add/edit form and page, can be
  filled from an import column, and are included in the data export
  (`custom_fields.csv`, `custom_field_values.csv`). Values are validated by
  type; a bad value is reported and not saved. Removing a field removes its
  values. Fields are never visible to, or editable by, another lab.

**Structured storage**
- Freezer / rack / box / position fields replace guessing at one free-text
  location (the free-text field stays as "other location notes", and older
  records keep showing it). New **Storage** page lists strains by freezer >
  rack > box and flags two strains recorded in the same position.

**Printable labels**
- `/labels`: pick strains (or one box from the Storage page), choose small
  (50x25 mm), standard (70x37 mm) or large (100x50 mm), and print. Each label
  has a QR code to the strain's page, its ID, name, accessions, storage
  position and lab name. Set `PUBLIC_BASE_URL` to the address your team uses
  so QR codes work from phones. New optional dependency: `segno` (labels
  print without a QR if it isn't installed). The QR was checked by decoding a
  rendered label.

**Audit fixes (found by testing the release itself)**
- **Cross-lab file leak (security):** `/uploads/...` and `/models/...` served
  any file to any signed-in user of *any* lab if they knew the filename, and
  model filenames are guessable. Files are now served only when a record
  belonging to the requesting lab points at them (others get a 404).
- **Cross-lab filename collision:** model files were named by collection ID,
  but IDs are only unique within a lab, so two labs with the same ID would
  overwrite each other's models. New model files carry the lab's id
  (`t<lab>-<ID>-...`). Existing files keep working under their old names.
- Deleting a strain or a plate photo now deletes its files from disk (they
  used to be left behind), including a photo's 3D relief.
- **Spreadsheet formula injection:** exports (CSV and Excel) neutralize cell
  text starting with `=`, `+`, `-`, `@` (real numbers like -80 are left alone).
  Matters now that data can arrive from imported spreadsheets.
- Exports are re-importable: the flat catalog now includes storage, media,
  source lab, and one column per custom field, using column names the
  importer recognizes (tested by exporting one lab and importing into a new
  one). The Excel export now includes the custom-field sheets.
- **Large imports:** a 5,000-row import failed with "database is locked" (the
  import opened a second database connection mid-write). Fixed; 5,000 rows
  now import in about a second. Covered by a test.
- **Upgrades:** the upgrade path was tested from real v2.0, v2.1, v3.0 and
  v4.0 databases. Two bugs fixed: an install from before accounts existed
  (v2.x) left every strain in no lab, invisible to everyone -- it now moves
  them into a "Default Lab" and creates a `labadmin` account (password printed
  once at start-up); and an upgraded admin named the same as the platform
  admin (e.g. `superadmin`) crashed start-up -- the platform account now gets
  a free name instead.
- Passwords: minimum 8 characters, not the username, not a well-known password
  (sign-up, admin-created accounts, admin resets). New **My account** page
  lets anyone change their own password.
- Catalog search now also looks at accessions, tags, storage location,
  source lab and custom-field values, and requires every typed word to match
  ("freezer 2 ale").
- Friendly error pages for 403/404/413 (upload too large)/500 instead of raw
  server pages; the 404 is the same whether a record is missing or belongs to
  another lab. Security headers added (nosniff, frame, referrer, no-store on
  private pages, HSTS when HTTPS cookies are on).
- The phone-access page (`/connect`) used a hard-coded `http://<ip>:5000`; it
  now uses the address you're actually using (or `PUBLIC_BASE_URL`). One QR
  library (`segno`) now serves both that page and labels; `qrcode` is no
  longer needed.

**Not done / known limits**
- Not tested on real label stock or a real label printer -- print one sheet
  on plain paper first and adjust the size.
- The catalog is one page: it renders 5,000 strains in about a quarter of a
  second but there is no pagination yet.
- Two people adding strains of the same species in the same instant could
  collide on the next collection ID (one gets an error page and retries).
- No Content-Security-Policy yet (pages use inline scripts and a CDN-hosted
  3D viewer).
- Imports can't update existing strains, only add new ones.

## v4.2: Fixed a real-photo relief bug: handwriting/label text baked in as false growth

- Tested the photo-driven 3D relief (build_plate_replica) against an actual
  plate photo for the first time (a WL Differential Agar plate with a
  handwritten strain label and the manufacturer's printed lot/expiry text
  on it) rather than only synthetic/seed data, and found that dark ink on a
  brightly colored indicator agar was read as colony growth just as
  strongly as the real colonies were: both the handwritten label and the
  printed text showed up as raised ridges in the height map, alongside a
  false halo at the dish's glass rim from its reflection.
- Root cause: `estimate_colony_intensity()`'s color-distance signal had no
  text/line-artifact suppression -- only the separate texture signal did,
  so anything with a strong color difference from the agar (ink, print,
  glare) counted as "colony" regardless.
- Fixed with a shape-based filter (`_keep_blob_like`, shared with the
  existing colony-counting code) applied to the combined signal: a real
  colony forms a compact, well-filled blob, while a pen stroke, printed
  character, or the rim's reflection forms a comparatively thin shape
  relative to its own bounding box, so these are now told apart by shape,
  not just color or texture. On the test plate, this took coverage from a
  misread "moderate growth across much of the plate" down to the correct
  "discrete, low-to-moderate raised colonies," and the handwritten label
  and printed text no longer appear as bumps in the exported model.
- This can under-count a genuinely thread-like/filamentous colony
  morphology, since classical shape heuristics can't always tell that
  apart from a line of handwriting -- noted in `_keep_blob_like()`'s
  docstring.

## v4.1: Realistic procedural 3D colony models

- The default 3D colony model (generated with no plate photo needed) no
  longer renders as one smooth plastic-looking dome. It's now a textured
  cluster of individually shaped, colored colonies scattered across agar,
  built the same way a real photo relief is: a height map plus a baked
  color texture, fed through the same mesh pipeline. Surface granularity,
  edge shape (entire, undulate, rhizoid/spreading), and gloss vs. matte
  finish are now per-species, so, for example, Bacillus subtilis renders
  as dry, wrinkled, spreading colonies while Pediococcus damnosus renders
  as small, glossy, mucoid ones.
- Single-cell models (the sphere/rod/ellipsoid shape per organism) are now
  colored to match the strain's Gram reaction where known -- purple for
  Gram-positive, pink-red for Gram-negative -- rather than a flat beige
  regardless of species.
- Colony layout uses a two-tier scatter (a few well-separated colony
  groups, each itself a small fused cluster of micro-colonies) so the
  model reads as distinct colonies on visible agar instead of one shape
  that fills the whole dish.
- Regenerating a species's model now reproduces the same layout each time
  (a stable seed derived from its collection ID) instead of reshuffling
  on every click.
- Species/strain colors and textures in `shape_config.py` are illustrative,
  chosen so distinct colonies are easy to tell apart, not a claim about a
  specific strain's real-world pigmentation -- except Gram-stain cell
  coloring, which follows the real crystal-violet/safranin reaction.

## v4.0: Multi-lab accounts (one install, many walled-off labs)

**Multi-lab accounts**
- One install can now host more than one lab. Each lab signs itself up at
  `/signup` (lab name, a username/password for its first account, and an
  optional checkbox to include a few example strains) and gets its own
  catalog, completely walled off from every other lab on the install --
  strains, photos, 3D models, revival logs, Gram stains, feedback and exports
  never cross between labs. No approval step: a
  lab is ready to use the moment it signs up.
- Three roles now, not two. **user** and **admin** work exactly as in v3.0,
  just scoped to the lab they belong to -- an admin's export, user list, and
  source-labs list only ever cover their own lab. A new **superadmin** role
  sits above all of that: one platform-level account, not scoped to any
  lab, that sees every lab's name and basic counts on `/superadmin` and can
  suspend or reactivate a lab, but is deliberately kept out of every
  ordinary route -- it can't open a lab's catalog, users, or source-labs
  list even if it tries the URL directly.
- `ADMIN_USERNAME`/`ADMIN_PASSWORD` now bootstrap that one platform
  superadmin on first run, not a lab's admin -- each lab creates its own
  first (admin) account through `/signup` instead. See README "Accounts &
  access" for the full breakdown.
- Collection IDs (`MDL-XXXXXX-NNN`) are now unique within a lab, not across
  the whole install -- two different labs can land on the same code for the
  same genus/species without colliding, since every lookup is scoped by
  which lab is logged in.
- Fresh installs no longer auto-seed example strains (there's no lab to own
  them yet before anyone signs up) -- seeding moved to the opt-in checkbox
  on `/signup` instead.
- Upgrading an existing v3.0 install: on first start, any accounts and
  catalog data from before multi-lab accounts existed are automatically
  moved into a new "Default Lab" tenant, so nothing is lost or orphaned --
  rename it any time from the Admin page.

## v3.0: Accounts & login, admin-only export, structured sample source

**Accounts & access control**
- The app now requires logging in. Two roles: **user** can fully add, edit,
  and delete their own strain/sample records -- revival logs, Gram stains,
  plate images, everything -- exactly as before login existed. **admin** can
  do all of that too, plus the handful of things that affect everyone's
  data or the tool's own configuration rather than one record: downloading
  the full data export (ZIP or Excel), and managing the Users and Labs
  lists (`/admin`).
- Accounts are named and individual, not a single shared password -- an
  admin creates, deactivates/reactivates, and resets passwords for other
  accounts from the Admin page. Passwords are never stored in plain text
  (salted hash, via werkzeug).
- On first run, a bootstrap admin account is created automatically and its
  username/password are printed to the console once. Set `ADMIN_USERNAME`
  / `ADMIN_PASSWORD` in `.env` beforehand to choose your own instead of a
  generated password (see README "Accounts & access").
- Safety guard: the last active admin account can't be deactivated, so the
  lab can never accidentally lock itself out of the Admin page.
- New admin-only Excel export (`/export/xlsx`, one sheet per table) next to
  the existing ZIP-of-CSVs export -- both now require an admin login, and
  both leave out the `users` table on purpose (password hashes never leave
  the app via either export, no matter who's exporting).

**Structured sample source: Lab or Online reference database**
- "Add strain" / "Edit strain" now ask where a sample came from: **Lab**
  (with an admin-curated dropdown of lab names, managed from `/admin`) or
  **Online reference database** (a free-text reference, e.g. "NCBI
  Reference Sequence Database"). Shown on the species page and editable
  like any other field.
- Not in the lab dropdown yet? An admin can add it from the Admin page --
  removing a lab from the list doesn't touch strains already recorded
  against it, it just stops offering that name for new entries.

## v2.1: In-app user feedback questionnaire

- Added a short, in-app feedback form (`/feedback`, linked from the nav bar
  and the landing page) so lab users can tell you what's working and what
  isn't, without leaving the app or needing a separate survey tool.
- Kept short on purpose: two required questions (an overall 1-5 rating
  and how easy the app was to use), plus three optional ones (which feature
  you use most, one thing to improve, and your name if you're open to a
  follow-up). Takes about two minutes.
- Responses are stored in the same local SQLite database as everything
  else -- no external form service, nothing leaves the lab's network.
- `/feedback/summary` shows the average rating, a breakdown of ease-of-use
  answers and most-used features, and the written comments (newest first)
  -- a quick way to see patterns across responses rather than reading one
  submission at a time.
- The `feedback` table is included in the one-click data export (both the
  in-app export and `export_catalog.py`), consistent with every other
  table: your feedback data is never locked into this tool either.

## v2.0: Per-plate media/conditions, multi-plate upload, optional 3D, and date+time on every log

**Gram stain image was missing from the add/edit strain forms**
- Reported bug: picking a Gram stain result on the "Add strain" or "Edit
  strain" page had no way to attach the stain image itself -- that was only
  possible afterward, from the separate "Log a Gram stain" form on the
  species page. Both forms now have an optional micrograph photo field
  (plus stain date/time and, on edit, who performed it) right next to the
  Gram stain dropdown.
- Attaching an image there creates a dated history entry exactly like the
  species-page form does, using the same shared save path, so the two
  routes can't drift out of sync. Saving the form again with no new image
  does **not** create a duplicate history entry -- only the dropdown value
  (the "current result") changes in that case.

**Plates can now record their own media and incubation conditions**
- Every plate image has its own free-text `media` (e.g. "YPD agar", "WLN
  agar") and `atmosphere`/conditions (e.g. "aerobic", "anaerobic", "5% CO2")
  field, shown on its card on the species page. Both fields are plain typed
  text with a suggestions dropdown (`<datalist>`) of common values already
  in the app -- the suggestions never restrict what you can type.
- Plate metadata (media, conditions, protocol, lighting, angle, resolution,
  background, captured by/date/time) can be **changed after the fact** from
  a "Change media / conditions / capture details" panel on each plate's
  card -- for when a plate was logged in a hurry or the wrong condition was
  picked. Deleting a plate image was already possible and still is.

**Upload now accepts multiple plates in one go**
- The upload form is no longer locked to one photo per submission: an "+
  Add another plate" button adds as many plate rows as you need (each with
  its own photo, media, and conditions), with a "Remove this plate" button
  on any row once there's more than one. Shared capture metadata (protocol,
  lighting, angle, captured by/date/time, etc.) still applies to every
  plate in that batch, since it's normally the same for one capture
  session, but each plate's media/conditions are independent.

**3D model generation is now optional and separate from upload**
- Uploading a plate photo no longer automatically builds a 3D relief.
  Instead, each plate's card gets a "Build 3D model (optional)" link --
  a plain `GET` request, so it's a normal link you can click, not a form --
  that builds the model only for that plate, only when you want one. This
  was a deliberate, explicit request to decouple the (slower, CPU-heavier)
  3D step from upload itself, especially useful when logging several
  plates at once and only caring about a 3D model for one of them.
  Auto-crop, coverage/elevation/margin estimation, and colony-count entry
  are unaffected -- those still happen (or stay manual, per v1.7) regardless
  of whether a 3D model is ever built.

**Date *and* time on every log, not just date**
- Revival checks, Gram stains, and plate captures each gained an optional
  `time` field alongside their existing date field (e.g. "stained at 14:30
  on 2026-10-08", not just "on 2026-10-08").
- Every logged entry already had a system `created_at` timestamp recording
  exactly when it was entered into the app; that's now shown in the UI too
  (labeled "Logged ...") next to the date/time the event itself happened,
  so there's a clear distinction between "when this happened in the lab"
  and "when it was recorded here."

## v1.9: Dark-theme calendar date-picker on every date field

- Every date field (revival date, Gram stain date, date stored, last
  verified, captured date) now opens a small calendar dropdown that matches
  the rest of the app, instead of the browser/OS's own date popup -- which
  on most platforms ignores this app's dark theme completely and shows up
  as a stark white box. Pick a day, jump months, or use the "Today"/"Clear"
  shortcuts; typing the date by hand still works too.
- Plain, dependency-free JavaScript (`static/calendar-picker.js`), no CDN
  or network call -- fits a self-hosted, LAN-only app. The value submitted
  is still a plain `YYYY-MM-DD` string, so nothing server-side changed.
- New date fields only need `class="date-input"` to get this automatically.

## v1.8: Updated logo

- Swapped in a sharper version of the logo (nav bar, hero, and all PWA/home-screen
  icons, including the maskable icon's safe-zone crop) -- same dark-theme-friendly
  cutout as before, just cleaner at every size.

## v1.7: Automatic colony counting removed; manual colony count for viability checks

- **Automatic colony counting from plate photos has been removed as a
  user-facing feature.** The v1.6 fix made the detector correctly ignore
  handwriting and printed labels, but validating it against a real lab
  photo also showed its harder remaining limitation plainly: colonies that
  are touching or otherwise merged in the photo can still be over- or
  under-split by a few, since that's a fundamentally harder segmentation
  problem. For a validation/QC context, an uncertain automatic number isn't
  good enough, so uploading a plate photo no longer reports a colony count
  or confidence at all -- it still auto-crops to the dish, estimates
  coverage/elevation/margin morphology, and builds the real 3D relief from
  the photo, exactly as before.
- **Colony count is now a plain, manually entered number**, recorded
  alongside each revival/viability check (date, performed by, growth
  result, contamination notes) on a strain's species page -- the same
  dated-history pattern already used for revivals. The strain's current
  count is shown on its viability card, and the full history (with counts)
  is shown in the revival log table.
- `plate_vision.detect_colonies()` (the scale-space blob detector fixed in
  v1.6) is kept in the codebase and still covered by its regression test
  (including the handwritten-label photo fixture) since it's a correct,
  useful piece of CV on its own -- it's just no longer wired into the live
  upload flow or trusted as an automatic count.
- Confirmed Gram stain tracking (result + optional micrograph photo, dated
  history, badge + catalog filter) is fully in place from v1.5 -- see below.

## v1.6: Fixed: handwriting and printed labels on a plate photo could be counted as colonies

- **Fixed a real counting bug**, found by validating the pipeline against an
  actual lab photo of a plate with a handwritten strain label and a printed
  date stamp: the colony-counting step (scale-space LoG blob detection) ran
  directly on the raw grayscale photo, completely bypassing the
  artifact-suppression logic that already existed for coverage/morphology
  reporting. Loops and curves in handwriting (and printed dot-matrix
  characters) are blob-shaped at the same scale as a real colony, so they
  were confidently counted as colonies -- in the validation photo, 6 real
  colonies came back as a confidently-reported 24.
- **Fix:** candidate colony blobs are now additionally screened by shape
  (compact, well-filled regions survive; thin, sparsely-filled ones -- ink
  strokes, grid lines -- are rejected), reusing and extending the
  artifact-suppressed colony-intensity signal rather than trusting raw pixel
  intensity alone. Confidence scoring was also corrected to stop being
  dragged down by leftover artifact area that the count correctly excludes.
- This plate photo is now a permanent regression fixture
  (`test_fixtures/handwritten_label_plate.jpg`), so this class of bug can't
  silently come back.
- **Still an open limitation** (tracked in the manuscript's Limitations
  section): colonies that are touching or otherwise merged in the photo can
  still be over- or under-split by a few, since that's a harder segmentation
  problem than artifact rejection. The pipeline already flags these cases as
  low-confidence/"worth a visual double-check" rather than reporting a
  falsely-precise number -- a real ground-truth benchmarking study (proposed
  in the manuscript) is the right way to quantify this precisely.

## v1.5: Gram stain tracking (result + micrograph)

- **Gram stain** is now a proper tracked field, not just free text: record a
  result (positive / negative / variable / not done), who stained it, the
  date, magnification, and an optional micrograph photo -- from the species
  page. Every stain is kept as dated history (a strain can be re-stained
  later without losing the earlier record), and the strain's current result
  shows as a color-coded badge on its own page and as a new "Gram" column
  and filter on the catalog.
- Gram stain results are included in the data export.
- Adding a strain now asks for its Gram stain result up front (optional);
  editing a strain uses the same controlled dropdown instead of free text,
  so values stay consistent across the catalog.

## v1.4: Logo polish

- **New logo**, cleanly cut out so it blends with the app's dark theme
  instead of showing as a pale rectangular box in the nav bar and hero.

## v1.3: Honest upload feedback

- **Fixed:** uploading a plate photo always said "...and built a 3D relief,"
  even on a machine where colony counting couldn't actually run (e.g.
  `opencv-python-headless` or `scipy` didn't install correctly) -- the
  failure was being silently swallowed. The upload page now says plainly
  what happened: a successful colony count, a relief built with counting
  unavailable (and why), or a build failure (and why), instead of a blanket
  success claim.
- The terminal now prints a short status line on startup: whether colony
  counting / the photo-driven 3D relief and QR-code phone access are each on
  or off, so a setup problem shows up immediately instead of being discovered
  later as a missing feature.
- **Easier settings:** copy `.env.example` to `.env`, fill in what you need and
  restart, instead of setting shell environment variables every time.

## v1.2: Traffic-light revival status

- Viability status is now color-coded everywhere it's shown (dashboard,
  species page, edit page): **green** = not due, **amber** = due within the
  next 5 days (an early-warning heads-up), **red** = due today or overdue.
- The "Strains needing revival" dashboard now also surfaces amber "due soon"
  strains, not just ones that are already due or overdue, so a revival can be
  planned for ahead of time instead of noticed only once it's late.

## v1.0: Microbial Digital Library (first version for the team)

**Rebrand**
- Renamed the app from "Microbial 3D Collection" to "Microbial Digital Library"
  and switched the collection-ID prefix from `MC3D-` to `mdl-`.
- New logo and a proper landing page (hero + feature tiles) separate from the
  dense browse/search catalog, which now lives at `/catalog`.

**Library vs. validation panel**
- Added `collection_type` (`library` / `validation` / `reference` / `other`)
  so the freezer strain library and the validation/QC biological-indicator
  panel can be browsed and filtered separately, while staying in one tool.
  Seeded with a Bacillus subtilis ATCC 6633 validation-panel example.

**Real photo -> 3D translation**
- Plate photos are no longer just pasted onto a generic disc: a classical
  computer-vision pipeline (OpenCV + scipy, no network calls) auto-crops to
  the dish boundary, estimates a colony-vs-background signal from color and
  local texture, and builds a genuine height-map relief of that photo's
  actual colony layout, with the photo baked on as a texture. Verified
  against real plate photos, including a dense mold plate, a printed
  counting grid, and a plate photographed through a gloved hand -- all of
  which previously confused the background estimate or got mistaken for
  colonies.
- Added a manual background-marker override for cases the automatic estimate
  still gets wrong.
- Any species without a defined 3D shape now still gets a reasonable
  stylized model, via a fallback chain, instead of silently generating
  nothing.

**Colony counting**
- Individual colonies are now counted and sized directly from the photo
  using scale-space blob detection, returned with a confidence score. On a
  confluent/overlapping plate, the app says plainly that colonies can't be
  reliably counted rather than returning a confident-looking wrong number.

**Viability / revival tracking**
- Strains can be marked `periodic`, `stable`, `longTerm`, or `unknown` for
  viability monitoring. Periodic strains get a revival interval; the landing
  page surfaces anything due or overdue, with who added the strain and the
  last revival result. Every revival check is logged (date, performed by,
  growth result, contamination notes) with full history on the species page.
- Due-date arithmetic is plain, deterministic code, because getting a date
  wrong here risks losing a culture.

**Attribution**
- Strains record who added them; plate uploads record who uploaded them and
  when; revival checks record who performed them. Also plain, deterministic
  fields, typed by the person doing the work.

**Smaller fixes**
- The free-text "brewing role" field allowed near-duplicate values (e.g.
  "souring/spoilage" next to "spoilage/souring"), which showed up as
  duplicate-looking entries in filters. It's now a canonical dropdown
  everywhere it's entered or edited.

**Sharing**
- `/connect` shows a QR code + LAN link so any device on the lab's network
  can open the same live catalog with no install. The app also installs as a
  home-screen PWA (icons + manifest + a service worker that only ever caches
  static assets, never catalog data, so an installed copy never looks stale).

