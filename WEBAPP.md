# LigaMagic Collection Comparer — web version

A small Flask wrapper around `collection.py`. Two collection IDs go in, the
listings from collection 2 whose card names also appear in collection 1 come
out, with a live progress log and a TSV download.

## Files

| File | Purpose |
| --- | --- |
| `app.py` | Flask server: `/` (page), `/stream` (SSE progress + results), `/tsv` (download) |
| `templates/index.html` | The whole front-end — no build step, no dependencies |
| `collection.py` | Scraping logic, still usable as a CLI |
| `requirements.txt` | Flask, Playwright, gunicorn |
| `Dockerfile` | Container build; honours `$PORT` so it works on any host |
| `probe.py` | One-off check: can the site be read without a browser? |
| `README.md` | Short project overview |

## Run it locally

```bash
pip install -r requirements.txt
playwright install chromium      # only needed once
python3 app.py
```

Then open <http://localhost:5001>.

Port 5000 is occupied by macOS AirPlay Receiver, so the default is 5001. To use
a different one:

```bash
python3 app.py --port 8080
```

The CLI still works exactly as before:

```bash
python3 collection.py 123456 654321
```

## Notes on behaviour

- The inputs accept a bare ID or a pasted collection URL.
- Progress lines stream in live over Server-Sent Events; matching rows appear
  as they're found.
- **Download TSV** returns only the header and data rows — no progress lines,
  no summary counts.
- The summary line under the results shows the counts (`total (unique)`) for
  both collections.

## Step 0 before deploying: is Chromium actually needed?

Playwright plus Chromium is what makes this app awkward to host — it needs a
container and a few hundred MB of RAM, which rules out the cheapest free tiers.
If LigaMagic serves the collection table as plain HTML, none of that is
necessary.

Run this once, with any real collection ID:

```bash
python3 probe.py 435257
```

It uses only the standard library, so there's nothing to install. It fetches the
page with normal browser headers and reports whether the card rows are in the
raw HTML.

- **"Playwright can be dropped"** — good news. The app becomes a plain HTTP
  fetch plus an HTML parser, small enough for any free host and much faster.
  Worth rewriting before deploying.
- **"Keep Playwright"** — the table is built by JavaScript (or the request was
  blocked), so we stay with the container approach below.

## Deploying to Render's free tier

Free, no credit card, and the `Dockerfile` and `render.yaml` here are already
set up for it. Render needs the code in a git repo, so that's the first step.

### 1. Put the folder on GitHub

Both routes below assume you've `cd`'d into this folder:

```bash
cd ~/Documents/ligascrape
```

macOS doesn't ship git until the Xcode command line tools are installed. If
`git --version` offers to install them, accept, then carry on. First time on a
machine, git also wants to know who you are:

```bash
git config --global user.name "Rafael Lemos"
git config --global user.email "rafaellemos42@gmail.com"
```

#### Easiest: the GitHub CLI

`gh` creates the remote repo, sets up authentication, and pushes in one step —
no tokens to paste.

```bash
brew install gh          # skip if you already have it
gh auth login            # choose GitHub.com → HTTPS → login with a browser

git init
git add .
git commit -m "LigaMagic collection comparer"
git branch -M main

gh repo create ligascrape --private --source=. --push
```

Use `--public` instead of `--private` if you don't mind the code being visible.
Render's free tier works with either.

#### Or: plain git

Create an empty repository at <https://github.com/new> — no README, no
.gitignore, since this folder already has them. Then:

```bash
git init
git add .
git commit -m "LigaMagic collection comparer"
git branch -M main
git remote add origin https://github.com/<your-username>/ligascrape.git
git push -u origin main
```

GitHub stopped accepting account passwords for pushes, so when it asks:
username is your GitHub username, and **password is a personal access token**,
not your real password. Create one at
<https://github.com/settings/tokens> → *Generate new token (classic)* → tick the
**repo** scope. Copy it immediately; it's only shown once.

#### Check what you're about to commit

```bash
git status --short
```

Everything listed should be code and docs. `.gitignore` already excludes
`.DS_Store`, `__pycache__/`, `probe_dump.html`, and `*.tsv` — that last one
keeps card exports and any old `have.tsv` / `want.tsv` out of the repo. If
something private shows up, add it to `.gitignore` before committing.

### 2. Create the Render service

1. Sign up at <https://dashboard.render.com/register> — GitHub login is easiest,
   since it also grants repo access.
2. Choose **New → Blueprint**, pick your repository, and Render will read
   `render.yaml` and propose a free web service. Approve it.

   If you'd rather not use the blueprint: **New → Web Service**, pick the repo,
   set Language to **Docker**, Instance Type to **Free**, and leave the rest.
3. The first build takes several minutes — the Playwright base image is around
   2 GB. Watch the **Logs** tab.
4. Your URL will be `https://ligamagic-comparer.onrender.com` (Render appends a
   suffix if that name is taken). Share it with your friends.

Pushing to `main` afterwards redeploys automatically.

### What to expect on the free tier

- **It sleeps** after 15 minutes with no traffic, and takes 30–50 seconds to
  wake. The first person to open it each day will wait; after that it's quick.
- **512 MB RAM is the real constraint**, because Chromium is memory-hungry. The
  scraper is set up to stay inside it: `--disable-dev-shm-usage` (a container's
  default 64 MB `/dev/shm` would otherwise crash Chromium outright), plus
  images, fonts and video are blocked at the network layer since none of the
  extracted data needs them.
- If a large collection does get killed mid-scrape, the logs will show an
  out-of-memory exit. That's the point at which dropping Playwright (see Step 0
  above) or moving to a bigger host becomes worth the effort.
- Free services also get 750 instance-hours a month, which one sleepy service
  won't come close to.

### Troubleshooting the build

**`ModuleNotFoundError: No module named 'playwright'`** — the Playwright base
image keeps its own copy of the package in root's user site-packages, which a
non-root process can't import. The Dockerfile installs it system-wide instead,
pinned via `ARG PLAYWRIGHT_VERSION` to match the `FROM` tag. If you ever bump
the image tag, bump that pin in the same commit: the image ships Chromium for
one specific Playwright version, and a mismatched package looks for a browser
revision directory that doesn't exist.

The build also runs a verification step, as the runtime user, that imports
playwright and checks the Chromium binary is really there. So this class of
problem now fails the build with a clear message rather than crash-looping the
container after deploy.

Note the image deliberately doesn't use `requirements.txt` — it installs pinned
versions directly. That file is for local installs only.

### If it misbehaves once deployed

The one risk that hosting can't design around: LigaMagic may rate-limit or
block requests from datacenter IP ranges. If the deployed version times out on
collections that work fine locally, that's the likely cause, and switching free
hosts probably won't fix it. The fallback in that case is running it at home.

## Running it from your phone without deploying

If you'd rather not host it, `app.py` already binds to `0.0.0.0`, so any device
on your home wifi can reach it at `http://<your-mac's-local-ip>:5001`. Find the
IP under System Settings → Network → Wi-Fi → Details.

This costs nothing and scrapes from your home IP, but your Mac has to be
powered on and awake, on the same network, with the script running — and your
friends won't be able to reach it. Deploying avoids all four constraints.
