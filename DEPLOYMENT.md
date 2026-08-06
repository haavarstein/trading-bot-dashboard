# Deployment Notes

## Static dashboard entrypoint
- `index.html` is the deployable dashboard page.
- `dashboard-prototype.html` is the original design snapshot.
- `vercel.json` enables simple static hosting settings.

## Safe to publish
Included files are dashboard/prototype/config/docs.
Excluded by `.gitignore`:
- `.env`
- local logs
- JSONL ledgers
- candidates.json
- kill switch

## Tomorrow plan
1. Publish repo to GitHub
2. Import to Vercel
3. Set production branch to `main`
4. Replace mock dashboard values with generated JSON from paper-trading bot
5. Add auto-refresh / scheduled regeneration
