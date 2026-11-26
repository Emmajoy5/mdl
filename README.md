# Microbial Digital Library: a self-hosted lab web app

[![tests](https://github.com/Emmajoy5/MDL/actions/workflows/tests.yml/badge.svg)](https://github.com/Emmajoy5/MDL/actions/workflows/tests.yml)
[![licence: MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](LICENSE)
<!-- After the first Zenodo release, add:
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.XXXXXXX.svg)](https://doi.org/10.5281/zenodo.XXXXXXX) -->

Version 4.7.2 · [Project page](https://emmajoy5.github.io/MDL/) ·
[Changelog](CHANGELOG.md) · [How to cite](#how-to-cite) · [Try the demo](#public-demo)

A browser-based tool for your lab to catalogue strains (both the freezer
library and the separate validation/QC panel), record exactly where each tube
is stored, keep plate photos with their conditions, and build a 3D colony
model from a plate photo when you want one. It runs on your own computer. No
SharePoint, no Power BI, and no cloud service is needed. Everything lives in this folder, on the computer where you run it.

It is free to use and change under the MIT licence (see "Licence" at the end).

## How people open it

The app runs on the computer where you start it. Which address you use depends
on who needs to reach it:

- **Just you, on one computer:** open `http://127.0.0.1:5000` (or
  `http://localhost:5000`). That address always means "this computer", so it
  works only for the person running the app. Each person who installs the app
  has their own separate copy.
- **A whole lab on the same network:** one computer runs the app and everyone
  else opens its network address, for example `http://192.168.1.20:5000`. The
  **Connect** page in the app shows the exact address and a QR code for phones.
- **Access from anywhere:** put the app on a server with a web address and
  HTTPS. See "Deploying for real use" below.

Each installation holds one lab. A second lab runs its own installation, with
its own data folder, so each lab keeps and manages its data itself.

## Quick start

```bash
# 1) install dependencies (once)
pip install -r requirements.txt
#    (or requirements-tested.txt for the exact versions the tests last passed with)

# 2) run
python app.py

# 3) open in your browser
#    on this machine:  http://localhost:5000
#    other lab machines on the same network:  http://<this-machine-ip>:5000
#    or scan the QR code at /connect from any phone on the same network
```

The app binds `0.0.0.0:5000`, so any lab machine on the same Wi-Fi/LAN can
reach it, and the catalog stays in sync for everyone; there's nothing to
install, and nothing per-device to fall out of date.

The first time it starts, the app prints a one-time setup code in the
terminal:

```
  Not set up yet. Open the address below and enter this setup code: USKV-WEEP
```

Open the app in a browser. It shows the setup page: enter the code, your lab's
name and the first admin account (tick the box if you want a few example
strains to look around with). The code is also saved as `setup-code.txt` in the
data folder and is deleted once the lab is set up. It stops anyone else on the
network setting up the lab before you do.

On a server or in Docker you can skip the page: set `LAB_NAME`,
`ADMIN_USERNAME` and `ADMIN_PASSWORD` in `.env` (see `.env.example`) before the
first start. Forgotten password? Use **Forgot your password?** on the log-in
page (see "Forgotten passwords" below).

## What it does

- **Landing page**: quick stats, strains due for a revival check, and
  shortcuts into the rest of the app.
- **Catalog**: search/filter by name, genus, brewing role, or collection
  type (library vs. validation/QC panel).
- **Species pages**: taxonomy, storage location, accessions, brewing
  relevance, viability/revival history, plate images, and 3D previews.
- **Bulk import** (admin): bring in an existing strain list from Excel or
  CSV, with automatic column matching, a preview, and duplicate detection.
- **Custom fields** (admin): add your own per-strain fields (text, number,
  date, pick-from-list) for anything the standard fields don't cover, so the
  library fits labs beyond brewing.
- **Storage map and labels**: record freezer / rack / box / position, see
  where everything is kept, and print QR-code labels for tubes and boxes.
  A new isolate must have a freezer or fridge and a box. Saving a position that
  another isolate already holds shows a warning, and the Storage page lists
  isolates that have no location at the top.
- **Upload plate images**: add one plate or several at once (an "+ Add
  another plate" button adds rows, each removable), every plate with its
  own photo, **media** (e.g. "YPD agar"), and **conditions**
  (aerobic/anaerobic/etc, free text with suggestions). Shared capture
  metadata (lighting, angle, scale-bar, protocol version, who uploaded it,
  captured date **and time**) applies to the whole batch. Uploads are
  checked by content (not just the file extension); TIFF and GIF photos are
  converted to PNG so every browser can show them. When you build a plate's
  3D model, the app:
  - finds the dish, then crops and centres on the agar bed itself, so the
    bench, a hand, a glove or the glass wall is not counted as growth,
  - removes writing and print on the plate (thin strokes, and dark marks
    that are not compact blobs, such as marker lines seen through the agar),
  - reports coverage, a density band and a margin description. These are
    visual aids on fixed colour scales (a blank plate reads as "no growth"),
    have not been benchmarked against manual scoring, and are not
    measurements. If a plate is fully covered by a lawn, mark a bare-agar
    point by hand.

  Building the 3D relief is a separate, optional step, a "Build 3D model"
  button on each plate's card, so a batch upload doesn't pay the (slower) 3D
  cost for plates you don't need a model for. A plate's media, conditions,
  and other capture details can also be changed after the fact (the change
  is recorded), or the photo voided, from its card on the species page.

  Colony counting used to also be automatic (scale-space blob detection on
  the photo), but it could misread handwriting or a printed label on the
  plate as colonies, and can't reliably split colonies that are touching.
  It's been removed as a user-facing feature; colony counts are now a
  plain number entered by hand (see below).
- **Viability / revival tracking**: mark a strain `periodic`, `stable`,
  `longTerm`, or `unknown`; periodic strains get a revival interval, and the
  dashboard surfaces anything due or overdue before a culture is lost, not
  after. The due date counts from the last passing check (or, before any
  check, from a "last revival before this app" date or the date stored). A
  failed check keeps the strain red until a passing check is logged, an older
  entry never moves the due date backwards, and future dates are
  refused. A periodic strain with no date or no interval is flagged rather
  than shown as "not due". Every revival check is logged with who did it, the result, an
  optional manual colony count (e.g. a CFU count on the viability plate),
  and the date **and time** of the check, kept as dated history alongside
  the rest of the revival log; each entry also shows exactly when it was
  recorded (separate from when the check itself happened) and the signed-in
  account that entered it.
- **History is kept**: revival checks, Gram stains and plate photos are never
  deleted. A wrong entry is *voided* with a reason; it stays on the strain
  page under "Voided entries" with who voided it and when. Edits to a strain
  are written to an audit log (old and new values), shown as the strain's
  change history and included in exports. Deleting a whole strain is an
  admin action that needs a reason, and a full copy of the record goes to the
  audit log first. Operator names typed on forms are still free text, so this
  is a laboratory record, not an electronic-signature system.
- **Gram stain tracking**: record a strain's Gram stain result (positive /
  negative / variable / not done), with an optional micrograph photo, who
  did the stain, the date and time, and magnification. The image field is
  available both from the dedicated "Log a Gram stain" form on the species
  page **and** right on the "Add strain" / "Edit strain" forms, next to the
  dropdown. Kept as dated history so a strain can be re-stained later, with
  the current result shown as a color-coded badge on the species page and
  the catalog (which can also be filtered by it).
- **Sample source**: record where a sample came from: **Lab** (picked from
  an admin-curated dropdown of lab names) or **Online reference database**
  (a free-text reference). Shown on the species page and editable from
  "Edit strain" like any other field.
- **Add species**: auto-assigns the next `MDL-XXXXXX-NNN` collection ID. The
  six letters come from the genus and species at the time the strain is added
  and are kept if the name later changes (for example *Lactobacillus brevis*,
  now *Levilactobacillus brevis*): an ID must never change, so treat the
  letters as a historical code, not as the current name. IDs are unique within
  a lab; cite them with the lab name when sharing records.
- **Generate models**: procedural cell + colony models for any species, even
  ones with no custom shape defined (a fallback chain always produces
  something reasonable). The colony model is a 90 mm plate with colonies at
  typical sizes; its printable STL is the same layout as the 3D view. The
  cell model shows the typical arrangement (pairs, chains, tetrads, budding
  yeast), coloured by the strain's recorded Gram result, with its real size
  stated. Colours and exact shapes are illustrative.
- **Connect on your phone**: QR code + LAN link. Over HTTPS the app also
  installs as a home-screen app (PWA) with no app-store step; browsers only
  allow that on HTTPS (or on the computer itself), so over plain
  `http://192.168...` it works as a normal web page. See "HTTPS on a lab
  network" below.
- **Export**: a one-click ZIP of the whole catalog (per-table CSVs + a flat
  CSV) or a single Excel workbook (one sheet per table), any time. Your data
  is never locked into this tool. Admin-only (see below); account
  passwords are never included in either export.
- **Feedback and evaluation**: two quick required questions (`/feedback`), plus
  an optional questionnaire for lab users, built so the answers can support a
  paper. It covers who answered (role, lab type, collection size), how cultures
  were tracked before, minutes to find or record a strain before and after, hours
  saved per week, mix-ups and overdue cultures the app caught, errors seen, how
  useful each feature was, the ten item System Usability Scale (scored 0 to 100),
  a likelihood to recommend score, and open comments. When a response is sent the
  app also measures the lab's real usage (strains, strains with a location, plate
  photos, revival checks, days in use, app version). A box asks permission to use
  the anonymous answers in research. Responses stay in the lab's own database.
  `/feedback/summary` shows the figures, and a lab admin can download a ZIP with
  `responses.csv` (anonymous, only consenting responses) and
  `data_dictionary.csv` for statistics or a supplement. Names and email addresses
  are never in that download. The System Usability Scale is by John Brooke (1996);
  cite it if you report scores.
- **Nothing leaves the lab**: the app sends no strain, photograph or search
  anywhere. Colony counts, revival due-dates and the record of who did what are
  plain, deterministic code and manual entry, so they behave the same way every
  time.

## Accounts & access

The app requires signing in (`/login`); there's no anonymous access, even on
your own network. Each installation holds one lab, set up on the first run (see
Quick start), and there is no account above the lab's own admins.

Two roles:

- **user**: the regular account for day-to-day lab work. Users add and edit
  strains, log revival checks, Gram stains and plate photos, and void a wrong
  entry with a reason. It is not a read-only account.
- **admin**: everything a user can do, plus what affects the whole lab: the
  data exports (ZIP and Excel), accounts and join requests, the source-labs
  list, custom fields, renaming the lab, deleting a strain and reading messages
  from the Contact page. All of this is on the **Admin** page (`/admin`).

**Adding people.** An admin can create an account under Users on the Admin
page. Or give a colleague the lab's **join code** from the Admin page (e.g.
`K7QM-4TXD`): they open "Ask to join" on the sign-in page, enter the code and
choose their own login, which stays inactive until an admin approves it (Admin
> Join requests, with a count badge in the menu). Knowing the code alone shows
no data. If the code leaks, "Make a new code" on the Admin page replaces it;
people who already joined are unaffected. Several people can be signed in at
once on different devices; they all work on the same database.

**About and Contact.** `/about` describes the tool and `/contact` has a message
form; both open without signing in and are linked from the menu and footer.
Messages appear in the **Messages** section of the Admin page. To
show your own details on the Contact page, set `CONTACT_NAME`, `CONTACT_ORG`,
`CONTACT_EMAIL`, `CONTACT_PHONE`, `CONTACT_WEBSITE` and `CONTACT_NOTE` in `.env`
(see `.env.example`); only what you set is displayed.

Passwords are never stored in plain text, and the `users` table (and its
password hashes) is never included in any data export, admin or not.
Passwords must be at least 8 characters, not the same as the username, and
not a well-known one (`password1` and the like). Anyone can change their own
from **My account** (click your username in the top bar). Changing or
resetting a password ends every other sign-in to that account.

### Forgotten passwords

The log-in page has a **Forgot your password?** link. The person enters their
username, or the email address saved on their account, and one of two things
happens:

- **With email.** If the account has an email address (people add one under
  My account) and the server can send mail (`SMTP_HOST`, `SMTP_FROM` and
  `PUBLIC_BASE_URL` set, see `.env.example`), a reset link is emailed. It works
  once, for 60 minutes. At most three are sent to one account in an hour.
- **Without email.** The lab's admins see the request under **Admin > Password
  help**, with a count in the menu. An admin checks it is really that person,
  clicks **Make reset link** and passes the link on in person or by a message
  only they read. It works once, for 24 hours. The admin never sees the new
  password.

The page gives the same answer whether or not the account exists. An admin can
also make a link at any time from the Users table, or type a new password with
**Set password** when the person is with them. A new link cancels older ones.
Using a link changes the password, clears any lockout and ends every other
sign-in. Every link made, used, cancelled or expired, and every password set by
an admin or on the server, is listed under **Recent password resets** on the
Admin page and written to the audit log.

If the lab's only admin is locked out, or nobody can sign in, use the server
commands, run on the machine that hosts the app:

```bash
python manage.py list-users
python manage.py reset-link USERNAME       # prints a single-use link, valid 24 hours
python manage.py reset-password USERNAME   # types a new password; also clears a lockout
python manage.py make-admin USERNAME       # if the lab's only admin has left
```

In Docker, put `docker exec -it <container>` in front of each command.

## Photo storage

Phone photos are often 3 to 15 MB. When a plate photo or a Gram stain photo is
uploaded, the app:

- turns it the right way up (phones often save photos sideways with a note to
  turn them, which browsers follow and the analysis did not);
- caps the long side at 4096 pixels, about 45 pixels per millimetre on a 90 mm
  plate, far more than the analysis or a screen needs;
- saves it as a JPEG at quality 90 with full colour resolution (4:4:4) and the
  colour profile kept, and removes the rest of the metadata, including the
  phone's GPS location.

A 12 megapixel photo takes about 40% of the space, and a 48 megapixel one about a tenth. On test photos the
plate analysis read the same colony coverage from the stored copy as from the
original (`photo_store.py` and `test_plates_and_cells.py` give the details).
Photos that are already small, upright and without a location are stored
untouched, and a compact photo is never encoded a second time.

To keep every photo exactly as uploaded, choose "Keep every photo exactly as
uploaded" under Admin, Photo storage. To shrink photos uploaded before this
version, run on the server (make a backup first):

```bash
python manage.py compact-photos           # lists what it would save
python manage.py compact-photos --apply   # does it
```

## Settings

Settings are read from the environment, or from a `.env` file in this folder if
you prefer to keep them together. Copy `.env.example` to `.env`, edit it, and
restart the app; it is picked up automatically. On startup the terminal prints a
short status line saying whether the photo 3D relief and QR code access are on,
so you can confirm a change took effect without clicking through the app.

## HTTPS on a lab network

Plain `http://` works, but passwords then cross the lab network unencrypted
and phones will not install the app. To serve HTTPS directly, give the app a
certificate and key:

```bash
MDL_SSL_CERT=cert.pem MDL_SSL_KEY=key.pem python app.py
```

A certificate your devices already trust is easiest (for example from your IT
team, or from a local certificate authority such as Caddy's internal CA; see
`deploy/Caddyfile.example`). A self-signed certificate works but each device
must accept it once. `MDL_PORT` and `MDL_HOST` set the port and address.

Printed QR labels point at the address you print them from. If you print on
the computer that runs the app (`localhost`), the codes use that computer's
address on the lab network instead, so phones on the same network can open
them. That address can change, so for labels you will keep, set
`PUBLIC_BASE_URL` to a stable address (a hostname rather than an IP) before
printing.

## Deploying for real use

`python app.py` uses waitress (installed from requirements.txt) when it is
available, and Flask's development server otherwise. `./run.sh` uses gunicorn
when it is installed. For anything outside your own network:

**1. Use a production server.** `wsgi.py` is the entry point:

```bash
pip install gunicorn
gunicorn --workers 1 --threads 8 --bind 127.0.0.1:8000 wsgi:app
```

Keep it to **one worker process** (threads are fine). The login rate limiter
keeps its per-IP counters in memory, so extra worker processes would each count
separately. Per-account lockout is stored in the database and is unaffected.

**2. Put HTTPS in front of it.** Run a reverse proxy (Caddy, nginx) that
terminates TLS and forwards to gunicorn. Caddy is the shortest route:

```
mdl.yourlab.org {
    reverse_proxy 127.0.0.1:8000
}
```

Once traffic is HTTPS, set these two environment variables (in `.env`):

```
FORCE_HTTPS_COOKIES=1      # session cookie is only ever sent over HTTPS
TRUST_PROXY_HEADERS=1      # use the proxy's X-Forwarded-For for rate limiting
```

Only set `TRUST_PROXY_HEADERS` when a proxy really is in front: otherwise a
client could fake that header and dodge the per-IP login limit. Leave
`FORCE_HTTPS_COOKIES` off for plain `http://` use, or logins won't stick.

**3. Or use Docker.** The included `Dockerfile` runs gunicorn and keeps
everything the app writes (database, photos, models, session key) under one
`/data` volume:

```bash
docker build -t microbial-digital-library .
docker run -d -p 8000:8000 -v mdl-data:/data \
  -e ADMIN_USERNAME=platform-admin -e ADMIN_PASSWORD=choose-a-real-password \
  microbial-digital-library
```

The Docker image is built and started automatically on every change to the
code. If it fails to build on your machine, please report it as a bug.

If you print labels, also pass `-e PUBLIC_BASE_URL=https://your-address` so
the QR codes open from phones (see `.env.example`).

Set `APP_DATA_DIR` yourself to get the same single-folder layout without Docker.

**4. Back it up.** `backup.py` writes one `.tar.gz` holding the database (copied
with SQLite's online-backup API, so it's consistent even while the app is
running), all photos, models, and the session key:

```bash
python backup.py --keep 14                  # keep the 14 newest
0 2 * * *  cd /path/to/app && python backup.py --keep 14     # nightly, in cron
```

Archives contain all of the lab's data, so store them as carefully as the
database. To restore, stop the app, unpack the archive into the data folder
(the app folder, or `APP_DATA_DIR`), and start it again. Copy archives off the
machine too; a backup on the same disk doesn't survive that disk failing.

**Built-in protections** (nothing to configure): every form carries a CSRF
token checked on every POST; an account locks for 15 minutes after 6 wrong
passwords in a row (an admin resetting the password unlocks it immediately);
and each IP address is limited to 20 login and 20 sign-up attempts per 5
minutes. Session cookies are `HttpOnly` and `SameSite=Lax`.

## Upgrading from an earlier version

Back up first (`python backup.py`). Then unpack the new version, bring across
your data (`microbe_catalog.db`, `uploads/`, `models/` and `.secret_key`,
or point `APP_DATA_DIR` at the folder that already holds them) and start
it. The database upgrades itself; nothing to run. This has been tested
starting from real v2.0, v2.1, v3.0 and v4.0 databases.

- From **v4.7.0**: accounts gain an optional email address and the tables for
  password resets. People who are signed in stay signed in until their password
  next changes.
- From **v4.0 to v4.6**: the platform `superadmin` account is removed, and the
  lab's own admins carry on as before. If the database holds more than one lab,
  the app will not start until it is split: `python manage.py list-labs`, then
  `python manage.py split-lab LAB_ID NEW_FOLDER` for each lab, and start each
  installation with `APP_DATA_DIR` pointing at its own folder.
- From **v3.x or earlier**: your strains and accounts move into a lab called
  "Default Lab" (rename it from the Admin page).
- From **v2.x** (before there were any accounts): the same, and because there
  was no login before, a `labadmin` account is also created for that lab. Its
  password is printed once in the start-up output; copy it down, sign in,
  then change it from **My account**.

## Troubleshooting: colony counting / 3D relief not showing up

Colony counting and the photo-driven 3D relief depend on `opencv-python-headless`,
`scipy`, and `Pillow` actually being installed; if `pip install -r requirements.txt`
didn't fully succeed on your machine, those features quietly don't run rather
than crashing the upload. Since v1.3, the upload page now tells you plainly
when this happens (instead of just not showing a colony count with no
explanation), and the startup message in the terminal says so too. If you see
that message, re-run `pip install -r requirements.txt` and check for errors,
then restart the app.

A 3D model is never built automatically: upload a plate photo, then use
the "Build 3D model (optional)" link on that plate's card (species page)
whenever you actually want one. Colony counts are a manual entry on a
revival/viability check, not tied to upload at all (see above).

## Files

| Path | Purpose |
|------|---------|
| `app.py` | The Flask web app (run this). |
| `microbe3d.py` | Mesh library: stylized shapes + photo-driven relief + STL/GLB exporters. |
| `scene3d.py` | The 3D viewer scene: glass Petri dish, agar and colonies built from many glossy beads, domes or branching arms (GLB only; the STL stays the printable relief). |
| `plate_vision.py` | Classical computer-vision analysis of plate photos (no network calls). |
| `shape_config.py` | Fallback chain for picking a 3D shape for any species. |
| `microbe_catalog.db` | SQLite catalog (auto-created/seeded if missing), also holds accounts and the labs list. |
| `.secret_key` | Auto-created session-signing key (keeps you logged in across restarts). Treat like a password; never share or commit it. |
| `templates/`, `static/` | HTML pages, CSS, logo, PWA manifest + service worker. |
| `uploads/` | Your uploaded plate images (saved here). |
| `models/` | Generated STL/GLB models + baked photo textures. |
| `schema.sql`, `seed_data.py` | Database schema and the example strain panel. |
| `model_builder.py` | Builds the procedural 3D files; shared by the app and `generate_models.py`. |
| `export_catalog.py` | Command-line export of the lab (same files as the Export button). |
| `generate_models.py` | Regenerate procedural models from the command line. |
| `build_catalog.py` | Static HTML dashboard of the lab (legacy). |
| `wsgi.py`, `Dockerfile`, `.dockerignore` | Production deployment (gunicorn / Docker), see "Deploying for real use". |
| `backup.py` | One-command backup of the database, photos and models. |
| `manage.py` | Server commands: list accounts, reset a password, make an admin, split an old multi-lab database. |
| `importer.py` | Parsing, column matching and validation for bulk CSV/Excel import (no web code). |
| `test_app.py` | Smoke test (`python test_app.py`). |
| `test_concurrent.py` | Several people saving at the same moment (`python test_concurrent.py`). |
| `test_demo.py`, `test_password_reset.py`, `test_plates_and_cells.py` | The other test suites; run each with `python`. |

## Notes & caveats

- **3D preview** uses Google `<model-viewer>` loaded from a CDN, so interactive
  3D preview needs internet on the *viewing* machine. Without it, the rest of
  the app still works and STL/GLB files remain downloadable.
- **Photo-driven models are visual estimates**, not calibrated measurements --
  useful for review and discussion, not a substitute for direct plate reading.
- **Example strains are illustrative and opt-in**: offered as a checkbox on
  the setup page ("add a few example strains"), not added otherwise, so
  a fresh lab starts with an empty catalog unless it asks for them. Confirm
  accessions/brewing values against real strain records if you do include
  them. The Bacillus subtilis ATCC 6633 entry is a validation/QC panel
  example, separate from the general library.
- **Backup** = copy `microbe_catalog.db` + `uploads/` + `models/` (accounts,
  the source-labs list and the catalog all live in `microbe_catalog.db`).
- To start fresh: delete `microbe_catalog.db`; the app rebuilds on next
  launch and shows the setup page again.

## Cell shapes and adding new species

The cell model's shape and arrangement are chosen in this order, and the
first that applies wins:

1. a correction made under "Change the cell shape or arrangement" on the strain
   page (it lasts until the next Gram stain with a shape)
2. the cell shape and arrangement recorded with the latest Gram stain
3. words in the strain's morphology notes, such as "rods in chains" or
   "cocci in clusters"
4. a built-in entry for the species (`BUILTIN_SHAPES` in `shape_config.py`)
5. the typical shape of the genus (`GENUS_CELLS`, 98 genera)
6. the name (a genus ending in "coccus" is round)
7. the Gram result, as a last guess

The best way to get the model right is to record what you see when you log a
Gram stain: the form asks for the cell shape and arrangement next to the result.

The strain page says which of these decided the shape. Shapes: coccus, rod,
coccobacillus, curved rod, spiral and yeast (oval). Arrangements: single,
pairs, chains, tetrads, clusters, packets of eight, palisades and budding.

To add a genus or species for everyone, add an entry to `GENUS_CELLS` or
`BUILTIN_SHAPES`. A species entry can also set the colony (flat / raised /
convex / umbonate / crateriform). A per-strain override can still go in the
`shape_config` field (JSON with `cell` and `colony`).

## Before you rely on it

This software is shared free of charge under the MIT licence, with no warranty
of any kind. Please read this section before you store anything important in it.

**Where the data lives.** The database, photographs and models are stored on
the computer or server that runs the application, and nowhere else. There are
three common set-ups. On one laboratory computer or a lab network, the data stay
inside the laboratory. On a rented or cloud server, the data are on that server
and in the care of its provider, so check where the provider stores data, how it
is protected and what its terms say, particularly for personal or commercially
sensitive data. If someone else runs the installation for you, that person can
open the files directly, so choose who runs it with care. The only data that leave the installation
are the copies you export or back up yourselves.

**Who is responsible for the data.** Each installation is run by the person or
organisation that installed it. Your database, photos and models stay on your
own computer or server. The author has no access to them, cannot see them, and
cannot recover them. Whoever runs an installation is responsible for the data in
it, for who is allowed to reach it, and for following the laws and lab rules
that apply to them, including rules on personal data (user names and email
addresses are stored for each account).

**No liability.** The author and contributors are not liable for lost, damaged
or wrongly recorded data, for unauthorised access, leaks or breaches of an
installation, for wrong decisions made from what the app shows, or for any
other loss that results from using the software. This applies whether the
installation is yours, a colleague's or a hosted copy someone else runs. The
full wording is in the `LICENSE` file.

**What you should do**
- **Back up.** Run `python backup.py` on a schedule and keep copies on a
  different disk or machine. Restore a backup once in a while to check that it
  works. A backup stored only on the same disk does not survive that disk failing.
- **Keep it private.** Use strong passwords. Do not expose the development
  server (`python app.py`) to the internet. If people outside your building need
  access, use a production server with HTTPS, as described under "Deploying for
  real use".
- **Protect the secret files.** Never share or publish `.env`, `.secret_key` or
  the `.db` file.
- **Update.** New versions fix problems. Back up first, then follow the upgrade
  steps below.
- **Check what matters.** The app is a record keeping aid. It is not an
  accredited quality system, and its plate analysis and 3D models are not
  calibrated measurements. Confirm anything that affects a decision, a
  customer or a regulator.

**Reporting a problem.** If you find a security problem, please do not post it
publicly. Follow the steps in `SECURITY.md`. Ordinary bugs and ideas are
welcome as issues on the repository.

## Licence

Released under the MIT licence. See the `LICENSE` file. You may use, copy,
change and share it, including in your own lab or business, as long as the
licence notice stays with it. It comes with no warranty.

## How to cite

If you use the Microbial Digital Library in your work, please cite it as:

> Fajoyegbe, E. Microbial Digital Library: a self-hosted digital catalogue for
> brewing and microbiology culture collections. Version 4.4.3. [Year].
> [Link to this repository]

## Feedback and contributions

Bug reports, feature ideas and questions are welcome. Open an issue on the
repository, or use the Contact page in the app. If you add a feature, run
`python test_app.py` and `python test_concurrent.py` first and make sure each ends
with "All checks passed."



## Taking plate photos the analysis can read

The automatic description and the 3D relief are only as good as the photo:
- Put the plate on a plain, matt surface: black card for pale colonies, white
  for dark ones. A hand, a glove or a patterned bench behind clear agar is read
  as part of the plate, and the page warns you when the background looks uneven.
- Photograph from straight above, with the whole dish in the frame and filling
  most of it.
- Use even light with no lamp reflected in the lid. Take the lid off if you can.
- Choose the right plate size when you upload (35, 60, 90, 100 or 150 mm). Every
  size rule in the analysis is in millimetres, so a 60 mm plate read as 90 mm
  makes pen strokes look thick enough to be colonies.
- Writing, print and a counting grid on the base are recognised and left out, but
  colonies sitting on top of a mark are harder to see.
- If writing, a label or glare still shows up as colonies, open "Fix the 3D for
  this photo" on the plate's card, paint over it, and save. Painted areas stay
  flat and are left out of the coverage figure.
- Small translucent colonies on a pale medium give little contrast. A dark
  background helps most.

The figures are visual aids, not measurements, and have not been benchmarked
against manual scoring. To help improve them, share a photo with the
"Plate photo for testing" issue template.

## Public demo

`MDL_DEMO=1` turns an installation into a public demo: one lab with the example
panel, real plate photos and 3D models, a shared demo account shown on the
sign-in page, account and user management switched off, and everything wiped and
rebuilt every few hours. `deploy/huggingface/README.md` explains how to host it
free on Hugging Face Spaces. Never enable it where real data lives.

## How to cite

Until the paper is published, cite the software release (see `CITATION.cff`;
GitHub shows a "Cite this repository" button). Each GitHub release is archived on
Zenodo with a DOI; see `docs/RELEASING.md`.

## Contributing

See `CONTRIBUTING.md`. Bug reports, plate photos the analysis gets wrong, and
pilot results from other labs are all welcome.
