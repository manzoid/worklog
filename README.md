# worklog

CLI that combines your **zsh history** and **Claude Code history** into a unified work session report. Shows when you were actually working, even when most of your activity happened inside Claude sessions (which don't appear in shell history).

## Setup

### 1. Enable zsh timestamp recording

worklog needs zsh's extended history format, which records a Unix timestamp with every command. Add this to your `~/.zshrc`:

```zsh
# Record timestamps in history
setopt EXTENDED_HISTORY
```

If you also want to tune history size (recommended):

```zsh
HISTFILE=~/.zsh_history
HISTSIZE=100000
SAVEHIST=100000
setopt EXTENDED_HISTORY       # Timestamps in history
setopt APPEND_HISTORY         # Append instead of overwrite
setopt INC_APPEND_HISTORY     # Write immediately, not on shell exit
setopt HIST_IGNORE_ALL_DUPS   # Deduplicate
```

Reload with `source ~/.zshrc`. New commands will have timestamps; old ones won't (this is fine — worklog skips entries without timestamps).

**To verify it's working:**

```bash
# Run a command, then check the last history entry:
fc -li -1
# Should show a date + time, e.g.:
#   16421  2026-03-27 12:00  ls
```

### 2. Claude Code history

Claude Code automatically writes `~/.claude/history.jsonl` with millisecond timestamps for every prompt you send. No setup needed — if you use Claude Code, you already have this file.

### 3. Install worklog

```bash
cd /path/to/worklog

# With uv (recommended)
uv tool install -e .

# Or with pip
pip install -e .
```

## Usage

```bash
# Default: this week (since Monday midnight), 30-min session gap
worklog

# Since a specific day
worklog --since wednesday
worklog --since yesterday
worklog --since today

# Since an exact date or datetime
worklog --since 2026-03-01
worklog --since 2026-03-01T09:00

# Adjust session gap threshold (minutes of inactivity = new session)
worklog --gap 15    # 15-minute gaps
worklog --gap 60    # 1-hour gaps

# Show only one source
worklog --source zsh
worklog --source claude

# Custom history file locations
worklog --zsh-history /path/to/.zsh_history
worklog --claude-dir /path/to/.claude
```

## How it works

1. Reads timestamps from your zsh history file (`: TIMESTAMP:0;command` format)
2. Reads timestamps from Claude Code's `history.jsonl` (one JSON object per prompt)
3. Merges and sorts all timestamps chronologically
4. Groups into "sessions" — a session ends when there's a gap longer than the threshold (default 30 min)
5. Prints a day-by-day breakdown with session times, durations, event counts, and which source(s) contributed

## Example output

```
Work sessions: Mon Mar 23 00:00 → Fri Mar 27 12:30
Session gap threshold: 30 minutes

  Mon Mar 23
    07:20–07:50    0h 29m  (   7 events, claude)
    09:14–11:31    2h 17m  (  90 events, claude)
    14:56–15:41    0h 44m  (  13 events, claude)
    17:09–17:55    0h 46m  (  31 events, claude)
                                                 Day: 4h 34m

  Tue Mar 24
    (no activity)

  Wed Mar 25
    06:10–07:10    0h 59m  (  42 events, claude+zsh)
    08:28–09:52    1h 23m  (  55 events, claude+zsh)
                                                 Day: 2h 22m

────────────────────────────────────────
  Mon Mar 23  4h 34m  ██████████████████
  Tue Mar 24  0h 00m
  Wed Mar 25  2h 22m  █████████
────────────────────────────────────────
       Total  6h 56m

  195 events (0 zsh-only, 141 claude-only, 54 mixed)
```

## Caveats

- **Lower bound on actual work time.** Time spent reading output, thinking, reviewing in a browser, writing in an editor, or in meetings is not captured.
- **Claude session granularity.** Claude history only records when *you send a prompt*, not when Claude finishes responding. Long-running Claude operations (multi-minute agent tasks) appear as a single timestamp.
- **zsh history only.** If you use bash or another shell alongside zsh, those commands won't be included. Pass `--zsh-history` to point at a different history file if needed.
