# Microbial Digital Library 4.7.2: installation guide for IT

This is the lab release of version 4.7.2. It is a standalone Python web application. It is not a SharePoint, Power Platform or Copilot component, so it runs on its own small server and the lab links to it.

## 1. What you need

- One small server or virtual machine on the internal network (Linux is the tested route). About 2 CPU cores, 2 GB of memory and 10 GB of disk is plenty for a lab collection.
- Python 3.10 or newer, or Docker.
- A DNS name and an HTTPS certificate for the address people will use, through your usual reverse proxy.
- Local disk for the data folder. Do not put the database on a network share, because SQLite is not safe there, and the write-ahead logging the app turns on does not work there either.
- Several people can use it at once. One person writes to the database at a time, so a save landing during someone else's spreadsheet import waits its turn (up to 30 seconds, `DB_BUSY_WAIT_MS`) rather than failing. Signing in writes too, so it waits the same way. An import of a few thousand rows takes well under a second.
- Outbound internet only for installing packages. Nothing is sent to the authors.

## 2. Choose one way to run it

**A. Docker (shortest).** The image is built and started by the project's GitHub Actions workflow; report any build error as a bug.

```
cp deploy/lab.env.example deploy/lab.env      # then edit lab.env
docker compose -f deploy/docker-compose.yml up -d --build
```

The app listens on 127.0.0.1:8000 and keeps everything in the volume `mdl-data`.

**B. Linux service without Docker.** Tested with gunicorn and the included backup script.

```
sudo useradd -r -m mdl
sudo mkdir -p /opt/mdl /var/lib/mdl && sudo chown mdl: /opt/mdl /var/lib/mdl
# copy this folder's contents into /opt/mdl, then:
sudo -u mdl python3 -m venv /opt/mdl/venv
sudo -u mdl /opt/mdl/venv/bin/pip install -r /opt/mdl/requirements.txt gunicorn
sudo -u mdl cp /opt/mdl/deploy/lab.env.example /opt/mdl/.env     # then edit .env
sudo cp /opt/mdl/deploy/mdl.service /etc/systemd/system/
sudo systemctl enable --now mdl
```

**C. Windows.** The production server (gunicorn) does not run natively on Windows. Use Docker Desktop (option A) or Ubuntu under WSL2 (option B). Flask's built-in server works for a short trial on a test machine only.

Then put the reverse proxy in front. Examples are in `deploy/Caddyfile.example` and `deploy/nginx.conf.example`. Keep the app on one worker process (threads are fine), because the login rate limiter keeps its counters in memory.

## 3. Settings

Everything is set in the `.env` or `lab.env` file, with the meaning of each line explained in `deploy/lab.env.example`. The ones that matter most:

- `LAB_NAME`, `ADMIN_USERNAME` and `ADMIN_PASSWORD` set up the lab and its first admin on first start only. Without them, the setup page does it.
- `PUBLIC_BASE_URL` is the address printed on QR labels. Without it, QR codes can point to the wrong host.
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY`, `SMTP_USERNAME`, `SMTP_PASSWORD` and `SMTP_FROM` let people who forget their password get a reset link by email (built from `PUBLIC_BASE_URL`, so set that too). Without them, lab admins make reset links from the Admin page, and `python manage.py reset-link USERNAME` makes one on the server.
- `FORCE_HTTPS_COOKIES=1` and `TRUST_PROXY_HEADERS=1` only when the HTTPS proxy is in place. With plain http, leave them off or logins will not stick.
- `APP_DATA_DIR` holds the database, photos, 3D models and the session key. It is the one folder to back up.

## 4. First start

1. If `ADMIN_USERNAME` and `ADMIN_PASSWORD` were set, sign in with them. Otherwise open the address: the setup page asks for the lab name, the first admin account and a setup code, which is in the app's log and in `setup-code.txt` in the data folder.
2. The lab admin creates accounts for colleagues, or shares the lab's join code so colleagues can ask to join and be approved.
3. One installation holds one lab. A second lab gets its own installation and data folder.
4. A forgotten password is reset on the server with `python manage.py reset-password USERNAME` (in Docker: `docker exec -it <container> python manage.py reset-password USERNAME`).

## 5. Backups and updates

- Run `python backup.py --keep 14 --dest /path/to/backups` nightly. It uses SQLite's online backup, so it is safe while the app runs. Archives contain every lab's data, so store them like the database and copy them off the machine.
- To restore, stop the app, unpack the archive into the data folder and start it again.
- To update, back up, replace the code folder with the new release, keep the same data folder and restart. The database upgrades itself. This was tested from four earlier releases.

## 6. Fitting in with SharePoint, Power BI and Copilot

- **SharePoint and Teams:** add a link or a quick link on the lab's SharePoint site. Embedding the app in a page or a Teams tab does not work as it is, because the app sends the header `X-Frame-Options: SAMEORIGIN` and uses `SameSite=Lax` cookies.
- **Power BI:** there is no live connector. A lab administrator can download a ZIP of CSV files with the lab's records. Those files can be placed in SharePoint and read by Power BI on a schedule.
- **Copilot:** not connected, and the app sends nothing to it.

## 7. Security facts for your review

- Passwords are stored hashed. Every form carries a CSRF token. An account locks for 15 minutes after 6 wrong passwords. Each IP address is limited to 20 login and 20 sign-up attempts per 5 minutes. Session cookies are HttpOnly and SameSite=Lax.
- One installation holds one lab. Only accounts that a lab admin has created or approved can sign in, and there is no account above the lab admins.
- Whoever can read the server's disk can read the lab's data, because the data is not encrypted at rest. Use disk encryption if your policy requires it.
- Not yet available: single sign-on and multi-factor authentication.
- No independent security audit has been done, and the tool is not validated for GLP or GMP records. The project's own tests (591 automated checks, plus the demo checks) pass on a clean installation.

## 8. Quick checks after installing

- The login page opens over HTTPS and the browser shows a valid certificate.
- You can sign in, add a strain, upload a plate photo and open its 3D view.
- A printed label's QR code opens the strain from a phone on the network.
- A backup file appears after running `backup.py`, and the data folder is the one you expect.

Problems and questions: see `SECURITY.md` for how to report a security issue, and `README.md` for the full documentation.
