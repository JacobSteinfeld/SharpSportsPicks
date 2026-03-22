#!/usr/bin/env python3
"""
AI Sports Betting Tracker
--------------------------
Track picks, log results, calculate ROI, and generate a daily context card
to paste into Claude for persistent memory across conversations.

Usage:
  python betting_tracker.py morning         → full morning routine (context card + daily prompt)
                                              MLB days: auto-detects and builds combined game+props prompt
  python betting_tracker.py lineup          → MLB lineup confirmation (run when lineups post, 1-6h before game)
  python betting_tracker.py night           → full night routine (log results + regenerate card)
  python betting_tracker.py add             → log a new pick
  python betting_tracker.py result          → update one pick interactively
  python betting_tracker.py resolve         → update ALL pending picks in one session
  python betting_tracker.py result 1 win    → update pick #1 inline, no prompts
  python betting_tracker.py result 2 loss   → update pick #2 inline, no prompts
  python betting_tracker.py context         → print today's context card for Claude
  python betting_tracker.py stats           → show full stats summary
  python betting_tracker.py list            → list recent picks
  python betting_tracker.py teams           → record + P&L broken down by team
  python betting_tracker.py pitchers        → MLB pitcher record + P&L (incl. marquee splits)
  python betting_tracker.py batters         → MLB batter prop record + handedness/prop-type splits
  python betting_tracker.py report          → generate weekly + all-time performance report
"""

import csv
import os
import re
import sys
from datetime import datetime, date

DB_FILE = "picks.csv"
FIELDS = ["id", "date", "sport", "game", "pick", "team", "bet_type", "odds", "units", "result", "pnl", "notes", "pitcher", "marquee", "batter", "prop_type", "prop_line", "batter_hand", "pitcher_hand"]


# ── DB helpers ────────────────────────────────────────────────────────────────

def init_db():
    if not os.path.exists(DB_FILE):
        with open(DB_FILE, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=FIELDS).writeheader()
        print(f"Created {DB_FILE}")
    else:
        # Migrate: add any missing columns to existing CSV
        with open(DB_FILE, newline="") as f:
            reader = csv.DictReader(f)
            existing_fields = reader.fieldnames or []
            rows = list(reader)
        new_fields = [f for f in FIELDS if f not in existing_fields]
        if new_fields:
            for row in rows:
                for field in new_fields:
                    row.setdefault(field, "")
            with open(DB_FILE, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=FIELDS)
                w.writeheader()
                w.writerows(rows)

def load_picks():
    with open(DB_FILE, newline="") as f:
        return list(csv.DictReader(f))

def save_picks(picks):
    with open(DB_FILE, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(picks)

def next_id(picks):
    if not picks:
        return 1
    return max(int(p["id"]) for p in picks) + 1


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_add():
    picks = load_picks()
    print("\n── Add new pick ──")
    sport    = input("Sport (NBA/NFL/MLB/NHL/etc): ").strip().upper()
    game     = input("Game (e.g. Lakers vs Celtics): ").strip()
    pick     = input("Your pick (e.g. Lakers -4.5): ").strip()
    team     = input("Team backed (e.g. Lakers / Over / Under): ").strip().title()
    bet_type = input("Bet type (spread/ml/total/parlay): ").strip().lower()
    odds     = input("Odds (e.g. -110, +150): ").strip()
    units    = input("Units risked (e.g. 1, 0.5, 2): ").strip()
    notes    = input("Notes / reasoning (optional): ").strip()

    pitcher     = ""
    marquee     = ""
    batter      = ""
    prop_type   = ""
    prop_line   = ""
    batter_hand = ""
    pitcher_hand = ""

    if sport == "MLB":
        is_prop = input("Batter prop? (y/n): ").strip().lower()
        if is_prop == "y":
            batter       = input("Batter name (e.g. Paul Goldschmidt): ").strip().title()
            prop_type    = input("Prop type (hits/total bases/HR/RBI/runs/strikeouts): ").strip().lower()
            prop_line    = input("Line (e.g. Over 1.5 / Under 0.5): ").strip()
            batter_hand  = input("Batter hand (L/R/S): ").strip().upper()
            pitcher_hand = input("Opposing pitcher hand (L/R): ").strip().upper()
            pitcher      = input("Opposing pitcher name (e.g. Paul Skenes): ").strip().title()
        else:
            pitcher = input("Starting pitcher (e.g. Paul Skenes): ").strip().title()
            marquee = input("Marquee pick? Featured play today? (y/n): ").strip().lower()
            marquee = "yes" if marquee == "y" else "no"

    pick_date = date.today().isoformat()
    new_pick = {
        "id":           next_id(picks),
        "date":         pick_date,
        "sport":        sport,
        "game":         game,
        "pick":         pick,
        "team":         team,
        "bet_type":     bet_type,
        "odds":         odds,
        "units":        units,
        "result":       "pending",
        "pnl":          "",
        "notes":        notes,
        "pitcher":      pitcher,
        "marquee":      marquee,
        "batter":       batter,
        "prop_type":    prop_type,
        "prop_line":    prop_line,
        "batter_hand":  batter_hand,
        "pitcher_hand": pitcher_hand,
    }
    picks.append(new_pick)
    save_picks(picks)
    print(f"\n✓ Pick #{new_pick['id']} saved: {pick} ({game})")


def calc_pnl(units, odds, result):
    """
    Calculate P&L using standard betting convention:
      Negative odds (-115): units = amount you want to WIN → risk = units * (abs(odds)/100)
      Positive odds (+150): units = amount you risk        → win  = units * (odds/100)
    """
    try:
        u = float(units)
        o = int(odds)
        if result == "win":
            return round(u if o < 0 else u * (o / 100), 2)
        elif result == "loss":
            return round(-u * (abs(o) / 100) if o < 0 else -u, 2)
        else:
            return 0.0
    except Exception:
        return 0.0


def apply_result(picks, pick_id, result):
    """Apply result to a pick by ID. Returns updated pick or None."""
    match = next((p for p in picks if str(p["id"]) == str(pick_id)), None)
    if not match:
        print(f"  ✗ Pick #{pick_id} not found.")
        return None
    if result not in ("win", "loss", "push", "void"):
        print(f"  ✗ Invalid result '{result}'. Use: win / loss / push / void")
        return None
    match["result"] = result
    match["pnl"] = calc_pnl(match["units"], match["odds"], result)
    print(f"  ✓ Pick #{pick_id} | {match['pick']} ({match['game']}) → {result.upper()} | P&L: {match['pnl']:+.2f}u")
    return match


def cmd_result():
    """
    Two modes:
      python betting_tracker.py result          → interactive (pick ID + result prompted)
      python betting_tracker.py result 1 win    → inline, no prompts
    """
    picks = load_picks()
    pending = [p for p in picks if p["result"] == "pending"]
    if not pending:
        print("\nNo pending picks.")
        return

    # Inline mode: python betting_tracker.py result <id> <outcome>
    if len(sys.argv) == 4:
        pick_id = sys.argv[2]
        result  = sys.argv[3].lower()
        apply_result(picks, pick_id, result)
        save_picks(picks)
        return

    # Interactive mode
    print("\n── Pending picks ──")
    for p in pending:
        print(f"  #{p['id']} [{p['date']}] {p['sport']} | {p['game']} | {p['pick']} @ {p['odds']} ({p['units']}u)")

    pick_id = input("\nEnter pick ID to update: ").strip()
    result  = input("Result (win/loss/push/void): ").strip().lower()
    apply_result(picks, pick_id, result)
    save_picks(picks)


def cmd_resolve():
    """
    Walk through ALL pending picks in one session.
    Shows each pick, asks win/loss/push, saves all at once.
    Paste agent result check output and type answers one by one.
    """
    picks = load_picks()
    pending = [p for p in picks if p["result"] == "pending"]
    if not pending:
        print("\nNo pending picks to resolve.")
        return

    print(f"\n── Resolving {len(pending)} pending pick(s) ──")
    print("Enter: win / loss / push / void   (or skip to leave pending)\n")

    updated = 0
    for p in pending:
        print(f"  #{p['id']} {p['sport']} | {p['game']}")
        print(f"       Pick: {p['pick']} @ {p['odds']} | {p['units']}u")
        answer = input("       Result: ").strip().lower()
        if answer == "skip" or answer == "":
            print("       → Skipped\n")
            continue
        if apply_result(picks, p["id"], answer):
            updated += 1
        print()

    save_picks(picks)
    print(f"── {updated} pick(s) resolved ──")

    # Auto-show stats after resolving
    settled = [p for p in picks if p["result"] in ("win", "loss", "push")]
    wins    = [p for p in settled if p["result"] == "win"]
    losses  = [p for p in settled if p["result"] == "loss"]
    pnl     = sum(float(p["pnl"]) for p in settled if p["pnl"] != "")
    wr      = (len(wins) / len(settled) * 100) if settled else 0
    print(f"\n  Overall: {len(wins)}-{len(losses)} ({wr:.1f}%) | P&L: {pnl:+.2f}u")
    print(f"\n  Run 'python betting_tracker.py context' to generate tomorrow's card.")


def cmd_stats():
    picks = load_picks()
    settled = [p for p in picks if p["result"] in ("win", "loss", "push")]
    wins    = [p for p in settled if p["result"] == "win"]
    losses  = [p for p in settled if p["result"] == "loss"]
    pending = [p for p in picks if p["result"] == "pending"]

    total_pnl = sum(float(p["pnl"]) for p in settled if p["pnl"] != "")
    win_rate  = (len(wins) / len(settled) * 100) if settled else 0

    print("\n══════════════════════════════")
    print("  AI SPORTS BETTING TRACKER  ")
    print("══════════════════════════════")
    print(f"  Total picks logged : {len(picks)}")
    print(f"  Settled            : {len(settled)}")
    print(f"  Pending            : {len(pending)}")
    print(f"  Wins               : {len(wins)}")
    print(f"  Losses             : {len(losses)}")
    print(f"  Win rate           : {win_rate:.1f}%")
    print(f"  Total P&L          : {total_pnl:+.2f} units")
    print("──────────────────────────────")

    # By sport
    sports = set(p["sport"] for p in settled)
    if sports:
        print("\n  By sport:")
        for sport in sorted(sports):
            sp = [p for p in settled if p["sport"] == sport]
            sw = [p for p in sp if p["result"] == "win"]
            sp_pnl = sum(float(p["pnl"]) for p in sp if p["pnl"] != "")
            wr = len(sw) / len(sp) * 100 if sp else 0
            print(f"    {sport:<6} {len(sw)}/{len(sp)}  {wr:.0f}%  {sp_pnl:+.1f}u")

    # Best / worst team
    teams = {}
    for p in settled:
        t = p.get("team", "").strip()
        if not t:
            continue
        if t not in teams:
            teams[t] = {"w": 0, "l": 0, "pnl": 0.0, "picks": 0}
        teams[t]["picks"] += 1
        teams[t]["pnl"]   += float(p["pnl"]) if p["pnl"] else 0
        if p["result"] == "win":
            teams[t]["w"] += 1
        elif p["result"] == "loss":
            teams[t]["l"] += 1
    if teams:
        best  = max(teams.items(), key=lambda x: x[1]["pnl"])
        worst = min(teams.items(), key=lambda x: x[1]["pnl"])
        print(f"\n  Best team  : {best[0]}  {best[1]['w']}-{best[1]['l']}  {best[1]['pnl']:+.2f}u")
        print(f"  Worst team : {worst[0]}  {worst[1]['w']}-{worst[1]['l']}  {worst[1]['pnl']:+.2f}u")

    # Best pitcher (MLB only)
    pitchers = {}
    for p in settled:
        if p["sport"] != "MLB":
            continue
        name = p.get("pitcher", "").strip()
        if not name:
            continue
        if name not in pitchers:
            pitchers[name] = {"w": 0, "l": 0, "pnl": 0.0}
        pitchers[name]["pnl"] += float(p["pnl"]) if p["pnl"] else 0
        if p["result"] == "win":
            pitchers[name]["w"] += 1
        else:
            pitchers[name]["l"] += 1
    if pitchers:
        best_p = max(pitchers.items(), key=lambda x: x[1]["pnl"])
        print(f"\n  Best pitcher: {best_p[0]}  {best_p[1]['w']}-{best_p[1]['l']}  {best_p[1]['pnl']:+.2f}u")

    print()


def cmd_teams():
    picks   = load_picks()
    settled = [p for p in picks if p["result"] in ("win", "loss", "push")]
    teams   = {}
    for p in settled:
        t = p.get("team", "").strip()
        if not t:
            continue
        if t not in teams:
            teams[t] = {"w": 0, "l": 0, "pnl": 0.0, "picks": 0, "sport": p["sport"]}
        teams[t]["picks"] += 1
        teams[t]["pnl"]   += float(p["pnl"]) if p["pnl"] else 0
        if p["result"] == "win":
            teams[t]["w"] += 1
        elif p["result"] == "loss":
            teams[t]["l"] += 1

    if not teams:
        print("\nNo team data yet. Team field added — will populate as you log picks.")
        return

    sorted_teams = sorted(teams.items(), key=lambda x: x[1]["pnl"], reverse=True)

    print("\n══════════════════════════════════════════════════")
    print("  TEAM TRACKER — If you followed every team play")
    print("══════════════════════════════════════════════════")
    print(f"  {'Team':<20} {'Sport':<7} {'W-L':<8} {'Win%':<7} {'P&L'}")
    print("  " + "─" * 46)
    for team, d in sorted_teams:
        total  = d["w"] + d["l"]
        wr     = d["w"] / total * 100 if total else 0
        arrow  = "▲" if d["pnl"] > 0 else "▼"
        print(f"  {team:<20} {d['sport']:<7} {d['w']}-{d['l']:<6} {wr:<6.0f}%  {arrow} {d['pnl']:+.2f}u")
    print()
    best  = sorted_teams[0]
    worst = sorted_teams[-1]
    print(f"  Best  : {best[0]} ({best[1]['w']}-{best[1]['l']}, {best[1]['pnl']:+.2f}u)")
    print(f"  Worst : {worst[0]} ({worst[1]['w']}-{worst[1]['l']}, {worst[1]['pnl']:+.2f}u)")
    print()


def cmd_pitchers():
    picks   = load_picks()
    mlb     = [p for p in picks if p["sport"] == "MLB" and p["result"] in ("win", "loss", "push")]
    pitchers = {}

    for p in mlb:
        name = p.get("pitcher", "").strip()
        if not name:
            continue
        if name not in pitchers:
            pitchers[name] = {"w": 0, "l": 0, "pnl": 0.0, "picks": 0,
                              "mq_w": 0, "mq_l": 0, "mq_pnl": 0.0, "mq_picks": 0}
        d   = pitchers[name]
        pnl = float(p["pnl"]) if p["pnl"] else 0.0
        d["picks"] += 1
        d["pnl"]   += pnl
        if p["result"] == "win":
            d["w"] += 1
        elif p["result"] == "loss":
            d["l"] += 1
        if p.get("marquee") == "yes":
            d["mq_picks"] += 1
            d["mq_pnl"]   += pnl
            if p["result"] == "win":
                d["mq_w"] += 1
            elif p["result"] == "loss":
                d["mq_l"] += 1

    if not pitchers:
        print("\nNo MLB pitcher data yet — pitcher field logs automatically on MLB picks.")
        return

    sorted_p = sorted(pitchers.items(), key=lambda x: x[1]["pnl"], reverse=True)

    print("\n══════════════════════════════════════════════════════════════════")
    print("  PITCHER TRACKER")
    print("══════════════════════════════════════════════════════════════════")
    print(f"  {'Pitcher':<22} {'W-L':<8} {'Win%':<7} {'P&L':<10} {'Marquee'}")
    print("  " + "─" * 62)

    for name, d in sorted_p:
        total = d["w"] + d["l"]
        wr    = d["w"] / total * 100 if total else 0
        arrow = "▲" if d["pnl"] >= 0 else "▼"

        mq_str = ""
        if d["mq_picks"] > 0:
            mq_wr = d["mq_w"] / (d["mq_w"] + d["mq_l"]) * 100 if (d["mq_w"] + d["mq_l"]) > 0 else 0
            mq_arrow = "▲" if d["mq_pnl"] >= 0 else "▼"
            mq_str = f"★ {d['mq_w']}-{d['mq_l']} ({mq_wr:.0f}%)  {mq_arrow} {d['mq_pnl']:+.2f}u"

        print(f"  {name:<22} {d['w']}-{d['l']:<6} {wr:<6.0f}%  {arrow} {d['pnl']:+.2f}u    {mq_str}")

    print("\n  ★ = Marquee pick (featured play of the day)")
    print()


def cmd_batters():
    picks   = load_picks()
    props   = [p for p in picks if p.get("batter","").strip() and p["result"] in ("win","loss","push")]

    if not props:
        print("\nNo batter prop data yet — logs automatically when you add an MLB batter prop pick.")
        return

    # Build per-batter stats
    batters = {}
    for p in props:
        name = p["batter"].strip()
        pt   = p.get("prop_type","").strip().lower()
        bh   = p.get("batter_hand","").strip().upper()
        ph   = p.get("pitcher_hand","").strip().upper()
        pnl  = float(p["pnl"]) if p["pnl"] else 0.0
        win  = p["result"] == "win"
        loss = p["result"] == "loss"

        if name not in batters:
            batters[name] = {
                "w":0, "l":0, "pnl":0.0,
                "props": {},          # by prop_type
                "vs_hand": {},        # by pitcher_hand
            }
        d = batters[name]
        d["pnl"] += pnl
        if win:  d["w"] += 1
        elif loss: d["l"] += 1

        # By prop type
        if pt:
            d["props"].setdefault(pt, {"w":0,"l":0,"pnl":0.0})
            d["props"][pt]["pnl"] += pnl
            if win:  d["props"][pt]["w"] += 1
            elif loss: d["props"][pt]["l"] += 1

        # By opposing pitcher hand
        if ph:
            label = f"vs {'LHP' if ph=='L' else 'RHP'}"
            d["vs_hand"].setdefault(label, {"w":0,"l":0,"pnl":0.0})
            d["vs_hand"][label]["pnl"] += pnl
            if win:  d["vs_hand"][label]["w"] += 1
            elif loss: d["vs_hand"][label]["l"] += 1

    sorted_b = sorted(batters.items(), key=lambda x: x[1]["pnl"], reverse=True)

    print("\n══════════════════════════════════════════════════════════════════════")
    print("  BATTER PROP TRACKER")
    print("══════════════════════════════════════════════════════════════════════")

    for name, d in sorted_b:
        total = d["w"] + d["l"]
        wr    = d["w"] / total * 100 if total else 0
        arrow = "▲" if d["pnl"] >= 0 else "▼"
        print(f"\n  {name}  —  {d['w']}-{d['l']} ({wr:.0f}%)  {arrow} {d['pnl']:+.2f}u")

        # Prop type splits
        if d["props"]:
            for pt, pd in sorted(d["props"].items(), key=lambda x: x[1]["pnl"], reverse=True):
                pt_total = pd["w"] + pd["l"]
                pt_wr    = pd["w"] / pt_total * 100 if pt_total else 0
                pt_arrow = "▲" if pd["pnl"] >= 0 else "▼"
                print(f"    {pt:<18} {pd['w']}-{pd['l']}  ({pt_wr:.0f}%)  {pt_arrow} {pd['pnl']:+.2f}u")

        # Handedness splits
        if d["vs_hand"]:
            for label, hd in sorted(d["vs_hand"].items()):
                h_total = hd["w"] + hd["l"]
                h_wr    = hd["w"] / h_total * 100 if h_total else 0
                h_arrow = "▲" if hd["pnl"] >= 0 else "▼"
                print(f"    {label:<18} {hd['w']}-{hd['l']}  ({h_wr:.0f}%)  {h_arrow} {hd['pnl']:+.2f}u")

    print()

    # Overall prop type summary
    all_props = {}
    for p in props:
        pt  = p.get("prop_type","").strip().lower()
        pnl = float(p["pnl"]) if p["pnl"] else 0.0
        if not pt: continue
        all_props.setdefault(pt, {"w":0,"l":0,"pnl":0.0})
        all_props[pt]["pnl"] += pnl
        if p["result"] == "win":  all_props[pt]["w"] += 1
        elif p["result"] == "loss": all_props[pt]["l"] += 1

    if all_props:
        print("  ── Overall by prop type ──────────────────────────────────────")
        for pt, pd in sorted(all_props.items(), key=lambda x: x[1]["pnl"], reverse=True):
            pt_total = pd["w"] + pd["l"]
            pt_wr    = pd["w"] / pt_total * 100 if pt_total else 0
            arrow    = "▲" if pd["pnl"] >= 0 else "▼"
            print(f"    {pt:<20} {pd['w']}-{pd['l']}  ({pt_wr:.0f}%)  {arrow} {pd['pnl']:+.2f}u")
        print()


def cmd_list():
    picks = load_picks()
    recent = picks[-20:][::-1]
    print(f"\n── Last {len(recent)} picks ──")
    for p in recent:
        status = p["result"].upper()
        pnl    = f"{float(p['pnl']):+.1f}u" if p["pnl"] else ""
        print(f"  #{p['id']} [{p['date']}] {p['sport']} | {p['game']:<30} | {p['pick']:<18} | {status:<7} {pnl}")
    print()


def build_context_card(picks):
    """Build and return the context card string from picks data."""
    settled = [p for p in picks if p["result"] in ("win", "loss", "push")]
    pending = [p for p in picks if p["result"] == "pending"]
    wins    = [p for p in settled if p["result"] == "win"]
    losses  = [p for p in settled if p["result"] == "loss"]
    total_pnl = sum(float(p["pnl"]) for p in settled if p["pnl"] != "")
    win_rate  = (len(wins) / len(settled) * 100) if settled else 0

    today = date.today()
    recent_settled = [
        p for p in settled
        if (today - date.fromisoformat(p["date"])).days <= 7
    ]

    sports = {}
    for p in settled:
        s = p["sport"]
        if s not in sports:
            sports[s] = {"w": 0, "l": 0}
        if p["result"] == "win":
            sports[s]["w"] += 1
        else:
            sports[s]["l"] += 1

    sport_lines = ", ".join(
        f"{s} {v['w']}-{v['l']}" for s, v in sorted(sports.items())
    )

    pending_lines = "\n".join(
        f"  - #{p['id']} {p['sport']} | {p['game']} | {p['pick']} @ {p['odds']}"
        for p in pending
    ) or "  None"

    recent_lines = "\n".join(
        f"  - [{p['date']}] {p['sport']} | {p['pick']} ({p['game']}) → {p['result'].upper()} {p['pnl']}u"
        for p in recent_settled[-5:][::-1]
    ) or "  None"

    return f"""
╔══════════════════════════════════════════════════════════╗
  CLAUDE CONTEXT CARD — {today.isoformat()}
  Paste this at the start of every new Claude conversation.
╚══════════════════════════════════════════════════════════╝

## My AI Sports Betting Tracker — Session Context

**Overall record:** {len(wins)}-{len(losses)} ({win_rate:.1f}% win rate) | Total P&L: {total_pnl:+.2f} units
**Sports breakdown:** {sport_lines if sport_lines else "N/A"}
**Total picks logged:** {len(picks)} ({len(settled)} settled, {len(pending)} pending)

**Recent results (last 5):**
{recent_lines}

**Currently pending picks:**
{pending_lines}

**My betting style:**
- I focus on value picks using advanced stats (not just public lines)
- Typical bet: 1 unit, sometimes 0.5u on riskier plays
- Sports I cover: [EDIT: e.g. NBA, NFL, MLB]
- I post daily picks on YouTube as an AI sports betting channel
- I want 3 picks per day with full reasoning, stats, and confidence level

**Instructions for Claude:**
You are my AI sports betting analyst. Use the record and pending picks above
as your memory of our ongoing work. When finding new picks, always output them
in this exact format so I can log them easily:

PICK #[N] | [SPORT] | [GAME] | [PICK] | [BET TYPE] | [ODDS] | [UNITS] | [REASONING]

Always check for injury news, recent form (last 5 games), line movement,
and home/away splits. Flag any picks above 1.5u as HIGH CONFIDENCE only.
"""


def cmd_context():
    picks = load_picks()
    today = date.today()
    card  = build_context_card(picks)
    print(card)
    ctx_file = f"context_card_{today.isoformat()}.txt"
    with open(ctx_file, "w") as f:
        f.write(card)
    print(f"\n✓ Context card saved to: {ctx_file}")


def cmd_morning():
    picks   = load_picks()
    settled = [p for p in picks if p["result"] in ("win", "loss", "push")]
    pending = [p for p in picks if p["result"] == "pending"]
    wins    = [p for p in settled if p["result"] == "win"]
    losses  = [p for p in settled if p["result"] == "loss"]
    total_pnl = sum(float(p["pnl"]) for p in settled if p["pnl"] != "")
    win_rate  = (len(wins) / len(settled) * 100) if settled else 0
    today = date.today()

    print(f"\n── Morning Routine — {today.strftime('%A, %B %d %Y')} ──\n")

    # Step 1: Record summary
    print(f"  Record   : {len(wins)}-{len(losses)} ({win_rate:.1f}%) | P&L: {total_pnl:+.2f}u")
    print(f"  Picks    : {len(picks)} total ({len(settled)} settled, {len(pending)} pending)")

    prev_pending  = [p for p in pending if p["date"] != today.isoformat()]
    today_pending = [p for p in pending if p["date"] == today.isoformat()]

    if prev_pending:
        print(f"\n  Open from previous days:")
        for p in prev_pending:
            print(f"    #{p['id']} [{p['date']}] {p['sport']} | {p['game']} | {p['pick']} @ {p['odds']}")
    if today_pending:
        print(f"\n  Already logged today ({len(today_pending)}):")
        for p in today_pending:
            print(f"    #{p['id']} {p['sport']} | {p['game']} | {p['pick']} @ {p['odds']}")

    # Step 2: Generate context card
    card     = build_context_card(picks)
    ctx_file = f"context_card_{today.isoformat()}.txt"
    with open(ctx_file, "w") as f:
        f.write(card)
    print(f"\n  ✓ Context card saved     → {ctx_file}")

    # Step 3: Build daily_prompt_today.txt
    # Detect sport from daily_focus.txt to determine which prompt(s) to load
    focus_content = ""
    focus_block   = ""
    if os.path.exists("daily_focus.txt"):
        with open("daily_focus.txt") as f:
            focus_content = f.read().strip()
        if focus_content:
            focus_block = f"\n\n════════════════════════════════════════════════════════════════\n  TODAY'S FOCUS / SPECIAL INSTRUCTIONS\n════════════════════════════════════════════════════════════════\n{focus_content}\n"
            print(f"  ✓ Focus notes injected   → daily_focus.txt")

    is_mlb = "MLB" in focus_content.upper()

    if is_mlb:
        # MLB day: build combined game + props prompt
        sep = "\n\n" + "═" * 66 + "\n"
        parts = [card, focus_block]
        for label, fname in [("SECTION 1 OF 2 — MLB GAME MODEL", "mlb_game_prompt.txt"),
                              ("SECTION 2 OF 2 — MLB PROPS MODEL", "mlb_props_prompt.txt")]:
            if os.path.exists(fname):
                with open(fname) as f:
                    content = f.read().replace("[INSERT DATE]", today.isoformat())
                parts.append(sep + f"  {label}\n" + "═" * 66 + "\n\n" + content)
                print(f"  ✓ {fname} loaded")
            else:
                print(f"  ✗ {fname} not found — skipping")

        daily_prompt = "\n".join(parts)
        prompt_file  = f"daily_prompt_{today.isoformat()}.txt"
        with open(prompt_file, "w") as f:
            f.write(daily_prompt)
        print(f"  ✓ MLB combined prompt    → {prompt_file}")

        # Generate lineup confirmation template for later today
        lineup_file = f"lineup_check_{today.isoformat()}.txt"
        if not os.path.exists(lineup_file):
            _write_lineup_check(lineup_file, today)
            print(f"  ✓ Lineup check template  → {lineup_file}")

        print(f"\n  ⚠️  MLB LINEUP TIMING:")
        print(f"     Lineups post 1–6 hours before first pitch.")
        print(f"     STEP 1 now → paste {prompt_file} into Claude for pitcher/park/weather analysis.")
        print(f"     STEP 2 later → run: python betting_tracker.py lineup")
        print(f"     This finalizes picks with confirmed lineups before placing bets.")

    else:
        # Non-MLB day: standard single-prompt build
        agent_file = "daily_picks_agent_prompt.txt"
        if not os.path.exists(agent_file):
            print(f"  ✗ {agent_file} not found — skipping daily prompt build")
        else:
            with open(agent_file) as f:
                agent_prompt = f.read()
            agent_prompt   = agent_prompt.replace("[INSERT DATE]", today.isoformat())
            daily_prompt   = card + focus_block + "\n\n" + agent_prompt
            prompt_file    = f"daily_prompt_{today.isoformat()}.txt"
            with open(prompt_file, "w") as f:
                f.write(daily_prompt)
            print(f"  ✓ Daily prompt saved     → {prompt_file}")
            print(f"\n  → Copy {prompt_file} and paste into Claude Agent to generate today's picks.")


def cmd_night():
    picks   = load_picks()
    pending = [p for p in picks if p["result"] == "pending"]
    today   = date.today()

    if not pending:
        print("\nNo pending picks to resolve.")
        print("\n──────────────────────────────────────────────────────────")
        switch = input("  Set a special focus for tomorrow's prompt? (y/n): ").strip().lower()
        if switch == "y":
            print("  Type your focus instructions (press Enter twice when done):")
            focus_lines = []
            while True:
                line = input()
                if line == "" and focus_lines and focus_lines[-1] == "":
                    break
                focus_lines.append(line)
            with open("daily_focus.txt", "w") as f:
                f.write("\n".join(focus_lines).strip())
            print(f"  ✓ Focus saved → daily_focus.txt")
        else:
            if os.path.exists("daily_focus.txt"):
                os.remove("daily_focus.txt")
            print("  → Standard prompt tomorrow. No focus set.")
        return

    print(f"\n── Night Routine — {today.strftime('%A, %B %d %Y')} ──")
    print(f"\nPending picks ({len(pending)}):")
    for p in pending:
        print(f"  #{p['id']} [{p['date']}] {p['sport']} | {p['game']} | {p['pick']} @ {p['odds']} ({p['units']}u)")

    print("\nPaste agent result output below.")
    print("Expected line format:  RESULT | Pick: [pick] | Final: [score] | Outcome: WIN/LOSS/PUSH")
    print("Press Enter twice when done.")
    print("─" * 60)

    lines = []
    while True:
        try:
            line = input()
            if line == "" and lines and lines[-1] == "":
                break
            lines.append(line)
        except EOFError:
            break

    # Parse "Pick: ... | ... | Outcome: WIN/LOSS/PUSH" lines
    parsed = {}
    for line in lines:
        outcome_m = re.search(r'Outcome:\s*(WIN|LOSS|PUSH|VOID)', line, re.IGNORECASE)
        pick_m    = re.search(r'Pick:\s*([^|]+)',                   line, re.IGNORECASE)
        if outcome_m and pick_m:
            parsed[pick_m.group(1).strip()] = outcome_m.group(1).strip().lower()

    # Match parsed results to pending picks by pick name
    updated        = 0
    unmatched      = []
    for p in pending:
        matched = False
        for pick_name, outcome in parsed.items():
            if pick_name.lower() in p["pick"].lower() or p["pick"].lower() in pick_name.lower():
                apply_result(picks, p["id"], outcome)
                updated += 1
                matched = True
                break
        if not matched:
            unmatched.append(p)

    # Manual fallback for anything that didn't auto-match
    if unmatched:
        label = "Couldn't auto-match" if parsed else "No parseable results found —"
        print(f"\n{label} {len(unmatched)} pick(s). Enter manually:")
        for p in unmatched:
            print(f"\n  #{p['id']} {p['sport']} | {p['game']} | {p['pick']} @ {p['odds']}")
            answer = input("  Result (win/loss/push/void/skip): ").strip().lower()
            if answer in ("skip", ""):
                print("  → Skipped")
                continue
            if apply_result(picks, p["id"], answer):
                updated += 1

    save_picks(picks)
    print(f"\n── {updated} pick(s) resolved ──")

    # Tonight's results
    tonight = [p for p in picks if p["date"] == today.isoformat() and p["result"] in ("win", "loss", "push")]
    tw      = sum(1 for p in tonight if p["result"] == "win")
    tl      = sum(1 for p in tonight if p["result"] == "loss")
    t_pnl   = sum(float(p["pnl"]) for p in tonight if p["pnl"] != "")

    # All-time
    settled  = [p for p in picks if p["result"] in ("win", "loss", "push")]
    wins     = [p for p in settled if p["result"] == "win"]
    losses   = [p for p in settled if p["result"] == "loss"]
    all_pnl  = sum(float(p["pnl"]) for p in settled if p["pnl"] != "")
    wr       = (len(wins) / len(settled) * 100) if settled else 0

    print(f"\n  Tonight  : {tw}-{tl} | P&L: {t_pnl:+.2f}u")
    print(f"  Overall  : {len(wins)}-{len(losses)} ({wr:.1f}%) | P&L: {all_pnl:+.2f}u")

    # Best sport
    sports = {}
    for p in settled:
        s = p["sport"]
        if s not in sports:
            sports[s] = {"pnl": 0.0, "w": 0, "l": 0}
        sports[s]["pnl"] += float(p["pnl"]) if p["pnl"] else 0
        if p["result"] == "win":
            sports[s]["w"] += 1
        else:
            sports[s]["l"] += 1
    if sports:
        best = max(sports.items(), key=lambda x: x[1]["pnl"])
        print(f"  Best sport: {best[0]} ({best[1]['w']}-{best[1]['l']}, {best[1]['pnl']:+.1f}u)")

    # Regenerate context card
    card     = build_context_card(picks)
    ctx_file = f"context_card_{today.isoformat()}.txt"
    with open(ctx_file, "w") as f:
        f.write(card)
    print(f"\n  ✓ Context card updated → {ctx_file}")
    print(f"  → Tomorrow's context card is ready.")

    # Regenerate Excel dashboard
    if os.path.exists("generate_excel.py"):
        import subprocess
        result = subprocess.run(["python3", "generate_excel.py"], capture_output=True, text=True)
        if result.returncode == 0:
            print(f"  ✓ Excel dashboard updated → SharpSportsPicks_Dashboard.xlsx")
        else:
            print(f"  ✗ Excel update failed: {result.stderr.strip()}")

    # Tomorrow's focus
    print("\n──────────────────────────────────────────────────────────")
    switch = input("  Set a special focus for tomorrow's prompt? (y/n): ").strip().lower()
    if switch == "y":
        print("  Type your focus instructions (press Enter twice when done):")
        focus_lines = []
        while True:
            line = input()
            if line == "" and focus_lines and focus_lines[-1] == "":
                break
            focus_lines.append(line)
        focus_text = "\n".join(focus_lines).strip()
        with open("daily_focus.txt", "w") as f:
            f.write(focus_text)
        print(f"  ✓ Focus saved → daily_focus.txt (will inject into tomorrow's prompt)")
    else:
        if os.path.exists("daily_focus.txt"):
            os.remove("daily_focus.txt")
        print("  → Standard prompt tomorrow. No focus set.")


def _write_lineup_check(filepath, today):
    content = f"""══════════════════════════════════════════════════════════════════
  SharpSportsPicks AI — MLB LINEUP CONFIRMATION
  Run this when official lineups post (1–6 hours before first pitch)
  Date: {today.isoformat()}
══════════════════════════════════════════════════════════════════

Official lineups are now posted. Paste this into Claude along with
the morning model output. Confirm the following before placing any bets:

────────────────────────────────────────────────────────────────
1. STARTING PITCHERS — confirm, don't project
────────────────────────────────────────────────────────────────
  Check every game of interest. If the starter changed from
  morning analysis → re-run the pitcher model for that game.
  A starter change is a GAME-CHANGING event. Do not ignore it.

────────────────────────────────────────────────────────────────
2. LINEUP CONFIRMATION (spots 1–5 for each team)
────────────────────────────────────────────────────────────────
  For any game or prop in the morning model:
  □ Is the batter confirmed in the lineup?
  □ What batting order position? (affects PA count)
  □ Any impact player (wRC+ > 125 vs hand) scratched?
    → 1 missing: downgrade team wRC+ vs hand by 5 pts
    → 2+ missing: downgrade by 10 pts
  □ Any switch hitter confirmed? Pull the correct hand split.
    (Switch hitters bat RH vs LHP, LH vs RHP)

────────────────────────────────────────────────────────────────
3. WEATHER UPDATE
────────────────────────────────────────────────────────────────
  Pull RotoWire one more time — rotowire.com/baseball/weather.php
  Wind can shift significantly from morning forecast.
  Wrigley Field especially: wind direction can flip.
  If weather changed by 1+ tier → recalculate convergence score.
  If wind flipped direction entirely → recalculate all adjustments.

────────────────────────────────────────────────────────────────
4. LINE MOVEMENT CHECK
────────────────────────────────────────────────────────────────
  Compare current lines to morning lines on DraftKings / FanDuel.
  Sharp money signal: line moved 3+ cents with 70%+ public other side
    → Fade the public, the sharp money is right.
  If our model edge narrowed to < 0.5 runs due to line move → pass.
  If line moved IN OUR FAVOR (more value now) → can increase units.

────────────────────────────────────────────────────────────────
5. LINE SHOPPING — always check before placing
────────────────────────────────────────────────────────────────
  Check: DraftKings | FanDuel | Caesars | BetMGM | PointsBet
  Even 5 cents difference on odds adds up over a 162-game season.
  Always take the best available number.

────────────────────────────────────────────────────────────────
MORNING MODEL OUTPUT (paste below):
────────────────────────────────────────────────────────────────
[PASTE MORNING ANALYSIS HERE]

────────────────────────────────────────────────────────────────
LINEUP / WEATHER CHANGES FROM MORNING:
────────────────────────────────────────────────────────────────
[NOTE ANY CHANGES HERE]

────────────────────────────────────────────────────────────────
FINAL CONFIRMED PICKS:
────────────────────────────────────────────────────────────────
[LIST FINAL PICKS AFTER CONFIRMATION]

══════════════════════════════════════════════════════════════════
After picks confirmed:  python betting_tracker.py add  (once per pick)
End of night:           python betting_tracker.py night
══════════════════════════════════════════════════════════════════
"""
    with open(filepath, "w") as f:
        f.write(content)


def cmd_lineup():
    today  = date.today()
    picks  = load_picks()
    today_pending = [p for p in picks if p["result"] == "pending" and p["date"] == today.isoformat()]

    print(f"\n── MLB Lineup Confirmation — {today.strftime('%A, %B %d %Y')} ──\n")
    print("  Official lineups have posted. Time to finalize picks.\n")

    if today_pending:
        print(f"  Today's pending picks:")
        for p in today_pending:
            print(f"    #{p['id']} | {p['sport']} | {p['game']} | {p['pick']} @ {p['odds']}")
        print()

    lineup_file = f"lineup_check_{today.isoformat()}.txt"
    if not os.path.exists(lineup_file):
        _write_lineup_check(lineup_file, today)
        print(f"  ✓ Lineup check template saved → {lineup_file}")
    else:
        print(f"  ✓ Lineup check template exists → {lineup_file}")

    print(f"\n  → Paste {lineup_file} into Claude along with the morning model output.")
    print(f"  → Confirm lineups, weather update, line movement, then log final picks.")
    print(f"  → Run: python betting_tracker.py add  (once per confirmed pick)\n")


def cmd_report():
    from datetime import timedelta
    picks   = load_picks()
    today   = date.today()
    week_start = today - timedelta(days=today.weekday())  # Monday

    settled  = [p for p in picks if p["result"] in ("win","loss","push")]
    pending  = [p for p in picks if p["result"] == "pending"]
    wins_all = [p for p in settled if p["result"] == "win"]
    loss_all = [p for p in settled if p["result"] == "loss"]
    pnl_all  = sum(float(p["pnl"]) for p in settled if p["pnl"])
    wr_all   = len(wins_all) / len(settled) * 100 if settled else 0

    # This week
    week = [p for p in settled if date.fromisoformat(p["date"]) >= week_start]
    week_w   = [p for p in week if p["result"] == "win"]
    week_l   = [p for p in week if p["result"] == "loss"]
    week_pnl = sum(float(p["pnl"]) for p in week if p["pnl"])

    # Best and worst pick all time
    best_pick  = max(settled, key=lambda p: float(p["pnl"]) if p["pnl"] else 0, default=None)
    worst_pick = min(settled, key=lambda p: float(p["pnl"]) if p["pnl"] else 0, default=None)

    # Best and worst pick this week
    best_week  = max(week, key=lambda p: float(p["pnl"]) if p["pnl"] else 0, default=None)
    worst_week = min(week, key=lambda p: float(p["pnl"]) if p["pnl"] else 0, default=None)

    # Sport breakdown — all time
    sports = {}
    for p in settled:
        s = p["sport"]
        sports.setdefault(s, {"w":0,"l":0,"pnl":0.0})
        sports[s]["pnl"] += float(p["pnl"]) if p["pnl"] else 0
        if p["result"] == "win":   sports[s]["w"] += 1
        elif p["result"] == "loss": sports[s]["l"] += 1

    # Bet type breakdown
    bet_types = {}
    for p in settled:
        bt = p["bet_type"].title()
        bet_types.setdefault(bt, {"w":0,"l":0,"pnl":0.0})
        bet_types[bt]["pnl"] += float(p["pnl"]) if p["pnl"] else 0
        if p["result"] == "win":   bet_types[bt]["w"] += 1
        elif p["result"] == "loss": bet_types[bt]["l"] += 1

    # Team breakdown — find cold/hot teams
    teams = {}
    for p in settled:
        t = p.get("team","").strip()
        if not t: continue
        teams.setdefault(t, {"w":0,"l":0,"pnl":0.0,"sport":p["sport"]})
        teams[t]["pnl"] += float(p["pnl"]) if p["pnl"] else 0
        if p["result"] == "win":   teams[t]["w"] += 1
        elif p["result"] == "loss": teams[t]["l"] += 1

    hot_teams  = [(t,d) for t,d in teams.items() if d["pnl"] > 0]
    cold_teams = [(t,d) for t,d in teams.items() if d["pnl"] < 0]
    hot_teams.sort(key=lambda x: x[1]["pnl"], reverse=True)
    cold_teams.sort(key=lambda x: x[1]["pnl"])

    def pick_line(p):
        if not p: return "N/A"
        return f"{p['pick']} ({p['game']}) → {p['result'].upper()} {float(p['pnl']):+.2f}u"

    def sport_arrow(pnl): return "▲" if pnl > 0 else "▼" if pnl < 0 else "→"

    report = f"""
╔══════════════════════════════════════════════════════════════════╗
  SharpSportsPicks AI — PERFORMANCE REPORT
  Generated: {today.strftime('%A, %B %d %Y')}
╚══════════════════════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  THIS WEEK  (since {week_start.strftime('%b %d')})
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Record     : {len(week_w)}-{len(week_l)}  ({len(week_w)/len(week)*100:.1f}% win rate)  P&L: {week_pnl:+.2f}u
  Best pick  : {pick_line(best_week)}
  Worst pick : {pick_line(worst_week)}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ALL TIME
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Record     : {len(wins_all)}-{len(loss_all)}  ({wr_all:.1f}% win rate)
  Total P&L  : {pnl_all:+.2f} units
  Picks      : {len(picks)} total  ({len(settled)} settled, {len(pending)} pending)
  Best pick  : {pick_line(best_pick)}
  Worst pick : {pick_line(worst_pick)}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  BY SPORT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""" + "\n".join(
        f"  {sport_arrow(d['pnl'])} {s:<8} {d['w']}-{d['l']}  ({d['w']/(d['w']+d['l'])*100:.0f}%)  {d['pnl']:+.2f}u"
        for s, d in sorted(sports.items(), key=lambda x: x[1]["pnl"], reverse=True)
    ) + f"""

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  BY BET TYPE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""" + "\n".join(
        f"  {sport_arrow(d['pnl'])} {bt:<12} {d['w']}-{d['l']}  ({d['w']/(d['w']+d['l'])*100:.0f}%)  {d['pnl']:+.2f}u"
        for bt, d in sorted(bet_types.items(), key=lambda x: x[1]["pnl"], reverse=True)
    ) + f"""

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  WHAT'S WORKING  ▲
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""" + ("\n".join(
        f"  ▲ {t:<20} {d['w']}-{d['l']}  {d['pnl']:+.2f}u  ({d['sport']})"
        for t, d in hot_teams[:5]
    ) or "  Not enough data yet.") + f"""

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  WHAT'S NOT  ▼  (transparency — we log everything)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""" + ("\n".join(
        f"  ▼ {t:<20} {d['w']}-{d['l']}  {d['pnl']:+.2f}u  ({d['sport']}) — avoiding until trend shifts"
        for t, d in cold_teams[:5]
    ) or "  Nothing in the red yet. Keep it that way.") + f"""

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  MODEL NOTES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  The model does not bet narratives. It bets edges.
  If the data says over when the public says under — we take the over.
  Every pick in this report was driven by projected score vs Vegas line,
  not by reputation, name value, or public perception.

  All picks are logged transparently — wins and losses.
  This record is real.

╔══════════════════════════════════════════════════════════════════╗
  SharpSportsPicks AI. The robot never sleeps.
  All picks for entertainment only. Gamble responsibly. 1-800-GAMBLER.
╚══════════════════════════════════════════════════════════════════╝
"""

    print(report)
    fname = f"report_{today.isoformat()}.txt"
    with open(fname, "w") as f:
        f.write(report)
    print(f"✓ Report saved → {fname}")


# ── Main ──────────────────────────────────────────────────────────────────────

COMMANDS = {
    "morning": cmd_morning,
    "lineup":  cmd_lineup,
    "night":   cmd_night,
    "add":     cmd_add,
    "result":  cmd_result,
    "resolve": cmd_resolve,
    "stats":   cmd_stats,
    "teams":    cmd_teams,
    "pitchers": cmd_pitchers,
    "batters":  cmd_batters,
    "report":   cmd_report,
    "list":     cmd_list,
    "context": cmd_context,
}

def main():
    init_db()
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        print("Commands:", ", ".join(COMMANDS.keys()))
        return
    COMMANDS[sys.argv[1]]()

if __name__ == "__main__":
    main()
