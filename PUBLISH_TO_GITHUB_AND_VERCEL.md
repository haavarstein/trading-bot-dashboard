# Publish to GitHub + Vercel

## Current state
This project is already prepared for deployment:
- local git repo initialized on `main`
- initial commit created
- deployable static page at `index.html`
- `vercel.json` present
- `.env` ignored and not tracked

## Repo target
- Repo name: `trading-bot-dashboard`
- Visibility: `public`

## What still requires authentication
### GitHub
This machine currently has:
- no `gh` CLI installed
- no GitHub token configured
- browser session not signed into GitHub

So the remaining publish step is either:
1. sign into GitHub in browser and create the repo, or
2. provide a GitHub PAT and use git HTTPS push

## Option A — quickest tomorrow (browser + one push)
1. Create repo on GitHub:
   - https://github.com/new
   - Repository name: `trading-bot-dashboard`
   - Visibility: Public
   - Do **not** initialize with README/.gitignore/license

2. In `~/trading-bot`, run:
```bash
git remote add origin https://github.com/haavarstein/trading-bot-dashboard.git
git push -u origin main
```

If prompted:
- Username: your GitHub username
- Password: a GitHub personal access token (not your GitHub password)

## Option B — PAT-based push
If you create a GitHub PAT with `repo` scope, then:
```bash
git remote add origin https://github.com/haavarstein/trading-bot-dashboard.git
git push -u origin main
```
Store credentials if desired:
```bash
git config --global credential.helper store
```

## Vercel
Global install is not required. `npx vercel` works here.

### Easiest path
1. Go to https://vercel.com/new
2. Import `haavarstein/trading-bot-dashboard`
3. Framework preset: Other
4. Root directory: `./`
5. Build command: leave empty
6. Output directory: leave empty
7. Deploy

Because this is a static site with `index.html`, Vercel should serve it directly.

## Alternative: Vercel CLI
From `~/trading-bot`:
```bash
npx vercel
```
Then follow prompts:
- link to your Vercel account
- select existing/new project
- confirm root `~/trading-bot`

For production:
```bash
npx vercel --prod
```

## Tomorrow follow-up
After paper account activation, next upgrade is to replace hardcoded dashboard values with generated JSON from the bot:
- broker snapshot from IBKR paper
- positions
- recent decisions
- benchmark vs SPY
- journal / improvements

Then redeploy and the dashboard becomes your live monitor.
