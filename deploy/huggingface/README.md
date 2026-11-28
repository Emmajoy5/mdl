# A free public demo on Hugging Face Spaces

Hugging Face Spaces runs Docker containers for free (a small CPU, no persistent
disk), which suits the demo mode: it rebuilds its own data at every start.

## Set up (once, about 10 minutes)
1. Create an account at https://huggingface.co and click **New Space**.
2. Name it (for example `mdl-demo`), choose **Docker** as the
   SDK and the **Blank** template, visibility **Public**, hardware **CPU basic (free)**.
3. In the Space's **Settings > Variables and secrets**, add these *variables*:

   | Name | Value |
   | --- | --- |
   | `MDL_DEMO` | `1` |
   | `PORT` | `7860` |
   | `APP_DATA_DIR` | `/tmp/mdl` |
   | `MDL_DEMO_RESET_HOURS` | `6` |
   | `MDL_SOURCE_URL` | `https://github.com/Emmajoy5/mdl` (or your own copy) |

4. Push the code to the Space (it is a git repository):
   ```bash
   git clone https://huggingface.co/spaces/<you>/<space-name> space
   git clone https://github.com/Emmajoy5/mdl
   cp -r MDL/* MDL/.dockerignore space/
   cp mdl/deploy/huggingface/SPACE_README.md space/README.md   # the Space needs this header
   cd space && git add -A && git commit -m "MDL demo" && git push
   ```
5. The Space builds the Dockerfile and starts. The sign-in page shows the demo
   account (`demo` / `demo-lab-2026`).

## What visitors can and cannot do
They can browse, add and edit strains, log revivals and Gram stains, upload plate
photos (8 MB per request), build 3D models, import a spreadsheet, export and print
labels. They cannot sign up, change the password, manage users or join codes,
rename the lab or use the contact form. Everything is wiped and
rebuilt at each start and every `MDL_DEMO_RESET_HOURS` hours.

A free Space sleeps after a period without visitors and takes about a minute to
wake; the first visit after that rebuilds the demo.

## Other hosts
Any host that runs a Dockerfile works the same way (Render, Fly.io, Railway,
Google Cloud Run): set the same variables. Never set `MDL_DEMO=1` on an
installation that holds real data: the demo deletes everything in `APP_DATA_DIR`.
