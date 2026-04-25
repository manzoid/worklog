# worklog

CLI that combines your **zsh history**, **Claude Code history**, **Codex history**, and **calendar meetings** into a unified work session report. Shows when you were actually working, even when most of your activity happened inside agent sessions (which don't appear in shell history) or in meetings (which don't generate any local activity at all).

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

### 3. Codex history

Codex automatically writes per-session rollout files under `~/.codex/sessions/YYYY/MM/DD/*.jsonl`. worklog extracts every `event_msg` with `payload.type == "user_message"` — the analog of Claude's per-prompt history. No setup needed.

### 4. Calendar meetings (optional)

worklog reads meetings from a local JSON cache at `~/.worklog/calendar.json`. It does NOT talk to any calendar service itself — that keeps worklog decoupled from Google Calendar, Outlook, iCloud, .ics files, or whatever else you use, and avoids the auth complexity of giving a CLI access to your calendar. Populate the cache however you want; worklog treats whatever is in the file as activity windows to fold into sessions.

Cache format:

```json
{
  "events": [
    {"start": "2026-04-08T13:30:00+09:00", "end": "2026-04-08T13:45:00+09:00", "summary": "Daily standup"}
  ]
}
```

Required fields: `start`, `end` (ISO 8601, with or without timezone). `summary` is optional. Any provider- or workflow-specific filtering (only billable meetings, only events you actually attended, only meetings tagged a certain way) happens at write time — write only what you want counted.

Each event becomes a continuous session whose bounds match the meeting's real start/end.

#### Refreshing the cache (for agents)

If you're using an LLM agent with calendar access (e.g. Claude Code with a Google Calendar MCP, an Outlook plugin, an .ics fetcher, etc.), give it instructions like:

> Fetch events from `<source>` for `<time range>`, apply this filter: `<your filter — e.g. "only events I marked billable", "only events I accepted", "only events tagged client-X">`, then overwrite `~/.worklog/calendar.json` with:
>
> ```json
> {"events": [{"start": "<ISO 8601>", "end": "<ISO 8601>", "summary": "<title>"}]}
> ```
>
> One object per kept event. `start` and `end` are required; `summary` is optional. Overwrite the file fully — worklog reads it as-is, no merging.

Worklog itself does not filter, so the agent must apply your filter at write time. Be explicit about the filter: provider-specific quirks (Google Calendar `colorId`, Outlook categories, .ics `X-` properties) live in the agent's instructions, not in worklog.

### 5. Install worklog

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
worklog --source codex
worklog --source cal

# Custom history file locations
worklog --zsh-history /path/to/.zsh_history
worklog --claude-dir /path/to/.claude
worklog --codex-dir /path/to/.codex
worklog --calendar-cache /path/to/calendar.json
```

## How it works

1. Reads timestamps from your zsh history file (`: TIMESTAMP:0;command` format)
2. Reads timestamps from Claude Code's `history.jsonl` (one JSON object per prompt)
3. Reads user-message timestamps from every Codex rollout file under `~/.codex/sessions/`
4. Reads meeting bounds from `~/.worklog/calendar.json` and emits periodic point events spanning each meeting (so the meeting becomes one continuous session of the right duration)
5. Merges and sorts all timestamps chronologically
6. Groups into "sessions" — a session ends when there's a gap longer than the threshold (default 30 min)
7. Prints a day-by-day breakdown with session times, durations, event counts, and which source(s) contributed

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
- **Claude/Codex session granularity.** Both sources only record when *you send a prompt*, not when the agent finishes responding. Long-running agent operations (multi-minute tasks) appear as a single timestamp.
- **zsh history only.** If you use bash or another shell alongside zsh, those commands won't be included. Pass `--zsh-history` to point at a different history file if needed.
