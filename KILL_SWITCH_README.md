# KILL SWITCH

## ⚠️ THIS FILE CONTROLS LIVE TRADING

**Current Status:** INACTIVE (file does not exist)

## To STOP all trading immediately:

1. Create this file:
   ```bash
   touch ~/trading-bot/KILL_SWITCH.txt
   ```

2. Or on Windows:
   ```cmd
   type nul > %USERPROFILE%\trading-bot\KILL_SWITCH.txt
   ```

3. The autotrader checks for this file EVERY cycle (every 15 minutes)

4. When detected:
   - All trading stops immediately
   - Telegram notification sent
   - No new orders will be placed
   - Existing positions are NOT automatically closed (manual action required)

## To RESUME trading:

1. Delete the kill switch file:
   ```bash
   rm ~/trading-bot/KILL_SWITCH.txt
   ```

2. Verify `autonomy_config.json` has `enabled: true`

3. Next cron cycle will resume operations

## Emergency Contact:

- Stop cron jobs: `hermes cronjob pause autotrader-cycle`
- List all jobs: `hermes cronjob list`
- View logs: `cat ~/trading-bot/data/*.jsonl`

## IMPORTANT:

- This only stops NEW orders
- Does NOT close existing positions
- Does NOT cancel pending orders (do that manually via IBKR TWS)
- Use this if you see unexpected behavior or want to pause the bot
