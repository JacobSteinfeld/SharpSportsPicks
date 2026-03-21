# SharpSportsPicks AI — Morning Routine
# Tell Claude Code: "run morning routine"

1. Read picks.csv and summarize:
   - Current overall record (wins, losses, win rate)
   - Total P&L in units
   - Any pending picks still open from previous days
   - breakdown by sport

2. Generate today's context card:
   - Build the full context card from picks.csv data
   - Include record, pending picks, recent results (last 5)
   - Save to context_card_today.txt in this folder

3. Build today's agent prompt:
   - Start with the context card content
   - Append the full deep analytics prompt below it
   - Fill in today's date automatically
   - Save the complete thing to daily_prompt_today.txt

4. Tell me:
   - Today's date and day of week
   - A one-line summary of my record so far
   - Confirm both files were saved
   - Remind me to copy daily_prompt_today.txt and paste into Claude Agent
