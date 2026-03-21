# SharpSportsPicks AI — Night Routine
# Tell Claude Code: "run night routine" then paste agent results

1. Read picks.csv and show all pending picks with IDs

2. Wait for me to paste the agent result check output

3. Parse each result from what I pasted:
   - Match each outcome to the correct pick ID in picks.csv
   - Update result field: win / loss / push
   - Calculate and store P&L for each pick automatically

4. Save updated picks.csv

5. Print updated stats:
   - New overall record
   - Tonight's results summary (what hit, what missed)
   - Total P&L update
   - Running P&L all-time

6. Generate updated context card:
   - Rebuild from the now-updated picks.csv
   - Save to context_card_today.txt (overwrites morning version)

7. Tell me:
   - Tonight's record (e.g. "went 2-2 tonight")
   - Best performing sport so far
   - Remind me that tomorrow's context card is ready
