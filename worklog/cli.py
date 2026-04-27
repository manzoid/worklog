#!/usr/bin/env python3
"""worklog - Unified work session report from zsh + Claude Code + Codex + calendar."""

import argparse
import datetime
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Session:
    start: int  # unix seconds
    end: int
    sources: set = field(default_factory=set)
    event_count: int = 0


def parse_since(value: str) -> datetime.datetime:
    """Parse a --since value into a datetime.

    Accepts:
      - "monday", "tuesday", ... (most recent past occurrence, midnight)
      - "today" (midnight today)
      - "yesterday" (midnight yesterday)
      - An ISO date like "2026-03-23"
      - An ISO datetime like "2026-03-23T09:00"
    """
    lower = value.lower().strip()
    now = datetime.datetime.now()
    today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if lower == "today":
        return today_midnight

    if lower == "yesterday":
        return today_midnight - datetime.timedelta(days=1)

    day_names = [
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ]
    if lower in day_names:
        target_weekday = day_names.index(lower)
        current_weekday = now.weekday()
        days_back = (current_weekday - target_weekday) % 7
        if days_back == 0:
            days_back = 0  # "monday" on a monday = today
        return today_midnight - datetime.timedelta(days=days_back)

    # Try ISO date / datetime
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.datetime.strptime(value, fmt)
        except ValueError:
            continue

    print(f"Error: cannot parse --since value: {value!r}", file=sys.stderr)
    print(
        "  Accepted: monday..sunday, today, yesterday, YYYY-MM-DD, YYYY-MM-DDTHH:MM",
        file=sys.stderr,
    )
    sys.exit(1)


def read_zsh_history(cutoff: int, histfile: Path) -> list[tuple[str, int]]:
    """Read zsh extended-history timestamps >= cutoff (unix seconds)."""
    events = []
    try:
        with open(histfile, "r", errors="replace") as f:
            for line in f:
                if line.startswith(": "):
                    parts = line.split(":")
                    if len(parts) >= 3:
                        try:
                            ts = int(parts[1].strip())
                            if ts >= cutoff:
                                events.append(("zsh", ts))
                        except ValueError:
                            pass
    except FileNotFoundError:
        print(f"Warning: zsh history not found at {histfile}", file=sys.stderr)
    return events


def read_claude_history(cutoff: int, claude_dir: Path) -> list[tuple[str, int]]:
    """Read Claude Code per-session transcript timestamps >= cutoff.

    Walks ~/.claude/projects/<sanitized-cwd>/<session-id>.jsonl and pulls
    every event that carries a top-level "timestamp" — user, assistant,
    tool_use, tool_result, system, etc. This captures agent run-time
    (multi-minute tool calls and reasoning), not just the moment you sent
    a prompt, so a long agent task is correctly counted as active work.
    """
    out: list[tuple[str, int]] = []
    projects_dir = claude_dir / "projects"
    if not projects_dir.exists():
        print(
            f"Warning: Claude transcripts not found at {projects_dir}",
            file=sys.stderr,
        )
        return out

    for path in projects_dir.rglob("*.jsonl"):
        try:
            if path.stat().st_mtime < cutoff:
                continue
        except OSError:
            continue
        try:
            with open(path, errors="replace") as f:
                for line in f:
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts_str = obj.get("timestamp")
                    if not ts_str or not isinstance(ts_str, str):
                        continue
                    try:
                        dt = datetime.datetime.fromisoformat(
                            ts_str.replace("Z", "+00:00")
                        )
                    except ValueError:
                        continue
                    ts = int(dt.timestamp())
                    if ts >= cutoff:
                        out.append(("claude", ts))
        except OSError:
            continue
    return out


def read_codex_history(cutoff: int, codex_dir: Path) -> list[tuple[str, int]]:
    """Read Codex rollout event timestamps >= cutoff (unix seconds).

    Walks ~/.codex/sessions/YYYY/MM/DD/*.jsonl and pulls every event with
    a top-level "timestamp" — user_message, agent_message, tool calls,
    reasoning, etc. This captures full agent run-time, not just the moment
    you sent a prompt.
    """
    out: list[tuple[str, int]] = []
    sessions_dir = codex_dir / "sessions"
    if not sessions_dir.exists():
        print(
            f"Warning: Codex sessions not found at {sessions_dir}",
            file=sys.stderr,
        )
        return out

    for path in sessions_dir.rglob("*.jsonl"):
        try:
            if path.stat().st_mtime < cutoff:
                continue
        except OSError:
            continue
        try:
            with open(path, errors="replace") as f:
                for line in f:
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts_str = obj.get("timestamp")
                    if not ts_str or not isinstance(ts_str, str):
                        continue
                    try:
                        dt = datetime.datetime.fromisoformat(
                            ts_str.replace("Z", "+00:00")
                        )
                    except ValueError:
                        continue
                    ts = int(dt.timestamp())
                    if ts >= cutoff:
                        out.append(("codex", ts))
        except OSError:
            continue
    return out


def read_calendar_cache(
    cutoff: int, cache_path: Path, gap: int
) -> list[tuple[str, int]]:
    """Read calendar events from a JSON cache and emit point events.

    For each meeting whose end >= cutoff, we emit periodic point events from
    start through end at half the session-gap cadence. This guarantees any
    two consecutive points are closer than the gap threshold, so the
    aggregator treats the meeting as one continuous session whose bounds
    match the meeting's real duration — even for meetings longer than the
    gap.

    Cache format: {"events": [{"start": ISO, "end": ISO, "summary": ...}, ...]}.
    The cache is populated out-of-band (e.g. via the Google Calendar MCP) —
    worklog itself does not talk to a calendar service.
    """
    out: list[tuple[str, int]] = []
    if not cache_path.exists():
        return out
    try:
        with open(cache_path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        print(
            f"Warning: could not read calendar cache at {cache_path}: {exc}",
            file=sys.stderr,
        )
        return out

    step = max(gap // 2, 60)  # half the gap, but never less than 1 minute
    for ev in data.get("events", []):
        start_str = ev.get("start")
        end_str = ev.get("end")
        if not start_str or not end_str:
            continue
        try:
            start_dt = datetime.datetime.fromisoformat(start_str)
            end_dt = datetime.datetime.fromisoformat(end_str)
        except ValueError:
            continue
        start_ts = int(start_dt.timestamp())
        end_ts = int(end_dt.timestamp())
        if end_ts < cutoff:
            continue
        ts = max(start_ts, cutoff)
        end_clamped = max(end_ts, cutoff)
        while ts < end_clamped:
            out.append(("cal", ts))
            ts += step
        out.append(("cal", end_clamped))
    return out


@dataclass
class Span:
    """A contiguous run of events from a single source within that source's
    own intra-gap. Used as the input to inter-source merging."""

    source: str
    start: int
    end: int
    event_count: int


def build_source_spans(
    events: list[tuple[str, int]], intra_gaps: dict[str, int]
) -> list[Span]:
    """Pass 1: build per-source spans.

    Each source's events are clustered using that source's own intra-gap.
    A 30-min silence in Claude's transcript (no tool calls firing) ends the
    Claude span even if zsh activity is happening — silence inside one tool
    is a real signal that you walked away from it.
    """
    by_source: dict[str, list[int]] = {}
    for source, ts in events:
        by_source.setdefault(source, []).append(ts)

    default_gap = intra_gaps.get("__default__", 15 * 60)
    spans: list[Span] = []
    for source, ts_list in by_source.items():
        ts_list.sort()
        gap = intra_gaps.get(source, default_gap)
        cur = Span(source=source, start=ts_list[0], end=ts_list[0], event_count=1)
        for ts in ts_list[1:]:
            if ts - cur.end > gap:
                spans.append(cur)
                cur = Span(source=source, start=ts, end=ts, event_count=1)
            else:
                cur.end = ts
                cur.event_count += 1
        spans.append(cur)
    return spans


def merge_spans(spans: list[Span], inter_gap: int) -> list[Session]:
    """Pass 2: merge spans across sources using the inter-source gap.

    Two spans (possibly from different sources) belong to the same session
    if the silence between them is <= inter_gap. This captures "I switched
    from Claude to a quick zsh check and back" as one continuous session.
    """
    if not spans:
        return []
    spans = sorted(spans, key=lambda s: s.start)
    sessions: list[Session] = []
    cur = Session(
        start=spans[0].start,
        end=spans[0].end,
        sources={spans[0].source},
        event_count=spans[0].event_count,
    )
    for span in spans[1:]:
        if span.start - cur.end > inter_gap:
            sessions.append(cur)
            cur = Session(
                start=span.start,
                end=span.end,
                sources={span.source},
                event_count=span.event_count,
            )
        else:
            cur.end = max(cur.end, span.end)
            cur.sources.add(span.source)
            cur.event_count += span.event_count
    sessions.append(cur)
    return sessions


def format_duration(seconds: int) -> str:
    """Format seconds as Xh YYm."""
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h}h {m:02d}m"


def print_report(sessions: list[Session], since: datetime.datetime, gap: int) -> None:
    """Print the formatted work session report."""
    if not sessions:
        print(f"No activity found since {since:%Y-%m-%d %H:%M}.")
        return

    # Header
    now = datetime.datetime.now()
    print(f"Work sessions: {since:%a %b %d %H:%M} → {now:%a %b %d %H:%M}")
    print(f"Session gap threshold: {gap // 60} minutes")
    print()

    # Group by day
    days: dict[str, list[Session]] = {}
    for sess in sessions:
        day_key = datetime.datetime.fromtimestamp(sess.start).strftime("%a %b %d")
        days.setdefault(day_key, []).append(sess)

    # Walk all days in range (including empty ones)
    day_cursor = since.date()
    end_date = now.date()
    grand_total = 0
    day_summaries: list[tuple[str, int]] = []

    while day_cursor <= end_date:
        day_key = day_cursor.strftime("%a %b %d")
        day_sessions = days.get(day_key, [])
        day_total = sum(s.end - s.start for s in day_sessions)
        grand_total += day_total
        day_summaries.append((day_key, day_total))

        if day_sessions:
            print(f"  {day_key}")
            for sess in day_sessions:
                dt_start = datetime.datetime.fromtimestamp(sess.start)
                dt_end = datetime.datetime.fromtimestamp(sess.end)
                dur = sess.end - sess.start
                src = "+".join(sorted(sess.sources))
                print(
                    f"    {dt_start:%H:%M}–{dt_end:%H:%M}"
                    f"  {format_duration(dur):>8}"
                    f"  ({sess.event_count:>4} events, {src})"
                )
            print(f"    {'':>48} Day: {format_duration(day_total)}")
            print()
        else:
            print(f"  {day_key}")
            print(f"    (no activity)")
            print()

        day_cursor += datetime.timedelta(days=1)

    # Summary table
    width = 40
    print("─" * width)
    for day_key, total in day_summaries:
        bar_chars = min(total // 900, 30)  # 1 char per 15 min, max 30
        bar = "█" * bar_chars
        print(f"  {day_key}  {format_duration(total):>8}  {bar}")
    print("─" * width)
    print(f"  {'Total':>10}  {format_duration(grand_total):>8}")

    # Source breakdown: per-source event share + how many events landed in
    # mixed-source sessions (i.e. you were active in more than one tool in
    # the same session window).
    all_sources = sorted({src for s in sessions for src in s.sources})
    per_source: dict[str, int] = {src: 0 for src in all_sources}
    mixed_events = 0
    for s in sessions:
        # event_count is total events in the session; we approximate per-source
        # share by splitting evenly across the session's sources. The exact
        # split isn't tracked because we collapse events into a Session as we
        # build it, so this is a fair-ish summary rather than an exact count.
        if len(s.sources) > 1:
            mixed_events += s.event_count
        share = s.event_count // max(len(s.sources), 1)
        remainder = s.event_count - share * len(s.sources)
        for i, src in enumerate(sorted(s.sources)):
            per_source[src] += share + (1 if i < remainder else 0)

    total_events = sum(s.event_count for s in sessions)
    parts = ", ".join(f"{per_source[src]} {src}" for src in all_sources)
    print()
    print(f"  {total_events} events ({parts}; {mixed_events} in mixed sessions)")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="worklog",
        description="Unified work session report from zsh + Claude Code history.",
    )
    parser.add_argument(
        "--since",
        default="monday",
        help=(
            "Start of reporting window. "
            "Accepts: monday..sunday, today, yesterday, YYYY-MM-DD, YYYY-MM-DDTHH:MM. "
            "Default: monday"
        ),
    )
    parser.add_argument(
        "--gap",
        type=int,
        default=15,
        help=(
            "Inter-source gap in minutes. Two activity spans from different tools "
            "merge into the same session if the silence between them is within this. "
            "Default: 15"
        ),
    )
    parser.add_argument(
        "--gap-zsh",
        type=int,
        default=15,
        help="Intra-source gap for zsh in minutes (sparse interactive shell). Default: 15",
    )
    parser.add_argument(
        "--gap-claude",
        type=int,
        default=2,
        help="Intra-source gap for Claude in minutes (dense transcript). Default: 2",
    )
    parser.add_argument(
        "--gap-codex",
        type=int,
        default=2,
        help="Intra-source gap for Codex in minutes (dense transcript). Default: 2",
    )
    parser.add_argument(
        "--zsh-history",
        default=None,
        help="Path to zsh history file. Default: $HISTFILE or ~/.zsh_history",
    )
    parser.add_argument(
        "--claude-dir",
        default=None,
        help="Path to Claude Code config directory. Default: ~/.claude",
    )
    parser.add_argument(
        "--codex-dir",
        default=None,
        help="Path to Codex config directory. Default: ~/.codex",
    )
    parser.add_argument(
        "--calendar-cache",
        default=None,
        help="Path to calendar JSON cache. Default: ~/.worklog/calendar.json",
    )
    parser.add_argument(
        "--source",
        choices=["all", "zsh", "claude", "codex", "cal"],
        default="all",
        help="Which sources to include. Default: all",
    )
    parser.add_argument(
        "--min-duration",
        type=int,
        default=0,
        help="Hide sessions shorter than this many minutes. Default: 0 (show all)",
    )

    args = parser.parse_args()

    since_dt = parse_since(args.since)
    cutoff = int(since_dt.timestamp())
    inter_gap = args.gap * 60
    intra_gaps = {
        "zsh": args.gap_zsh * 60,
        "claude": args.gap_claude * 60,
        "codex": args.gap_codex * 60,
        # Calendar events are emitted as periodic points spanning each meeting,
        # so any reasonable gap > the emission step (= inter_gap / 2) keeps the
        # meeting as one continuous span.
        "cal": inter_gap,
        "__default__": inter_gap,
    }

    histfile = Path(
        args.zsh_history or os.environ.get("HISTFILE", str(Path.home() / ".zsh_history"))
    )
    claude_dir = Path(args.claude_dir or (Path.home() / ".claude"))
    codex_dir = Path(args.codex_dir or (Path.home() / ".codex"))
    calendar_cache = Path(
        args.calendar_cache or (Path.home() / ".worklog" / "calendar.json")
    )

    # Collect events
    events: list[tuple[str, int]] = []
    if args.source in ("all", "zsh"):
        events.extend(read_zsh_history(cutoff, histfile))
    if args.source in ("all", "claude"):
        events.extend(read_claude_history(cutoff, claude_dir))
    if args.source in ("all", "codex"):
        events.extend(read_codex_history(cutoff, codex_dir))
    if args.source in ("all", "cal"):
        events.extend(read_calendar_cache(cutoff, calendar_cache, inter_gap))

    spans = build_source_spans(events, intra_gaps)
    sessions = merge_spans(spans, inter_gap)
    if args.min_duration > 0:
        min_secs = args.min_duration * 60
        sessions = [s for s in sessions if (s.end - s.start) >= min_secs]
    print_report(sessions, since_dt, inter_gap)


if __name__ == "__main__":
    main()
