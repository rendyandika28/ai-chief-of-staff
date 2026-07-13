"""Proactivity v2 helpers — schedule-conflict detection, pre-meeting KG
enrichment, and a small seen-store so each conflict is surfaced once.

Pure where possible: find_conflicts does no I/O so it's trivially testable.
"""

import json
from datetime import timedelta
from pathlib import Path

DEFAULT_DUR = timedelta(minutes=60)  # assumed length when an event has no end
URGENT_HOURS = 36                    # conflict within this window → interruptible
TIGHT_GAP_MIN = 10                   # < this gap between meetings → "tight" transition


def find_conflicts(events: list, loops: list, now) -> list:
    """events: dicts from calendar fetch_events (start/end aware, timed, summary, id).
    loops: [{"text", "due": datetime}] from OpenLoops.due_items.
    Returns conflict dicts: {kind, urgent, when, text}. No LLM, no I/O."""
    timed = sorted((e for e in events if e.get("timed")), key=lambda e: e["start"])
    horizon = now + timedelta(hours=URGENT_HOURS)
    out, seen = [], set()

    def add(kind, when, text, key):
        if key in seen:
            return
        seen.add(key)
        out.append({"kind": kind, "urgent": when <= horizon, "when": when, "text": text})

    def end_of(e):
        return e["end"] or (e["start"] + DEFAULT_DUR)

    # Meeting × meeting overlap: sorted by start, so later events with
    # start < a_end overlap a.
    for i, a in enumerate(timed):
        a_end = end_of(a)
        for b in timed[i + 1:]:
            if b["start"] >= a_end:
                break
            add("overlap", a["start"],
                f"bentrok: '{a['summary']}' ({a['start']:%a %H:%M}) nabrak "
                f"'{b['summary']}' ({b['start']:%H:%M})",
                ("overlap", frozenset((a["id"], b["id"]))))

    # Tight back-to-back: consecutive pair, small non-negative gap. Info-only
    # (never urgent) so it only ever rides along in a brief.
    for a, b in zip(timed, timed[1:]):
        gap = (b["start"] - end_of(a)).total_seconds() / 60
        if 0 <= gap < TIGHT_GAP_MIN:
            out.append({
                "kind": "tight", "urgent": False, "when": b["start"],
                "text": f"jeda mepet: '{a['summary']}' kelar {end_of(a):%H:%M} "
                        f"terus '{b['summary']}' {b['start']:%H:%M} ({int(gap)}m)"})

    # Deadline × meeting: an open-loop due falls inside a meeting window.
    for lp in loops:
        due = lp["due"]
        for e in timed:
            if e["start"] <= due <= end_of(e):
                add("deadline_clash", due,
                    f"deadline '{lp['text']}' ({due:%a %H:%M}) jatuh pas "
                    f"meeting '{e['summary']}'",
                    ("deadline", lp["text"], e["id"]))
                break

    return out


def relevant_facts(kg, embedder, user_id: str, ctx: str, k: int = 2) -> list:
    """Formatted KG facts genuinely related to a meeting context, or [] if none
    clear the relevance bar. Embeds the context once (or keyword fallback)."""
    if not ctx.strip():
        return []
    qvec = embedder.embed_one(ctx, "RETRIEVAL_QUERY") if embedder else None
    return kg.relevant(user_id, qvec, ctx, k=k)


# ---- conflict seen-store: each conflict pinged once ----------------------
def _seen_path(base: str = "memory/conflicts_seen.json") -> Path:
    return Path(base)


def load_seen(base: str = "memory/conflicts_seen.json") -> dict:
    try:
        return json.loads(_seen_path(base).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def unseen_conflicts(conflicts: list, now, base: str = "memory/conflicts_seen.json") -> list:
    """Filter to conflicts not yet stamped. Prunes keys whose time has passed."""
    seen = {k: v for k, v in load_seen(base).items() if v >= now.isoformat()}
    _seen_path(base).write_text(json.dumps(seen))  # persist the prune
    return [c for c in conflicts if _key(c) not in seen]


def mark_seen(conflicts: list, base: str = "memory/conflicts_seen.json"):
    """Stamp conflicts as surfaced, keyed with their 'when' as the expiry."""
    seen = load_seen(base)
    for c in conflicts:
        seen[_key(c)] = c["when"].isoformat()
    _seen_path(base).write_text(json.dumps(seen))


def _key(c: dict) -> str:
    return f"{c['kind']}|{c['text']}"


def _demo():
    """Self-check: python -m app.agent.proactive — no network."""
    import tempfile, os
    from datetime import datetime, timezone
    WIB = timezone(timedelta(hours=7))
    now = datetime(2026, 7, 13, 9, 0, tzinfo=WIB)

    def ev(eid, summary, h, m, dur_min=60):
        s = now.replace(hour=h, minute=m)
        return {"id": eid, "summary": summary, "timed": True,
                "start": s, "end": s + timedelta(minutes=dur_min)}

    # two overlapping meetings today → one overlap, urgent
    c = find_conflicts([ev("a", "Sync A", 10, 0), ev("b", "Sync B", 10, 30)], [], now)
    overlaps = [x for x in c if x["kind"] == "overlap"]
    assert len(overlaps) == 1 and overlaps[0]["urgent"], f"expected 1 urgent overlap, got {c}"

    # non-overlapping, far apart → no overlap
    c = find_conflicts([ev("a", "A", 10, 0), ev("b", "B", 14, 0)], [], now)
    assert not [x for x in c if x["kind"] == "overlap"], "far-apart meetings shouldn't overlap"

    # tight gap 5 min → tight, never urgent
    c = find_conflicts([ev("a", "A", 10, 0, 30), ev("b", "B", 10, 35)], [], now)
    tights = [x for x in c if x["kind"] == "tight"]
    assert len(tights) == 1 and not tights[0]["urgent"], f"expected 1 non-urgent tight, got {c}"

    # deadline falls inside a meeting → deadline_clash
    loops = [{"text": "kirim proposal", "due": now.replace(hour=13, minute=30)}]
    c = find_conflicts([ev("m", "Meeting Klien", 13, 0, 60)], loops, now)
    assert any(x["kind"] == "deadline_clash" for x in c), f"expected deadline_clash, got {c}"

    # future conflict (5 days out) → not urgent
    future = now + timedelta(days=5)
    fe = lambda eid, s, mm: {"id": eid, "summary": s, "timed": True,
                             "start": future.replace(hour=10, minute=mm),
                             "end": future.replace(hour=11, minute=mm)}
    c = find_conflicts([fe("a", "A", 0), fe("b", "B", 30)], [], now)
    assert c and not c[0]["urgent"], "conflict 5 days out must not be urgent"

    # seen-store: unseen once, then stamped
    base = os.path.join(tempfile.mkdtemp(), "seen.json")
    conf = find_conflicts([ev("a", "Sync A", 10, 0), ev("b", "Sync B", 10, 30)], [], now)
    conf = [x for x in conf if x["urgent"]]
    assert unseen_conflicts(conf, now, base) == conf, "first pass: all unseen"
    mark_seen(conf, base)
    assert unseen_conflicts(conf, now, base) == [], "second pass: all seen"

    print("proactive self-check OK")


if __name__ == "__main__":
    _demo()
