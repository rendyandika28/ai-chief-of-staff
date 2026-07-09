"""Test suite for AI Chief of Staff (native tool-use architecture).
Run: python3 tests/test_all.py

All DBs live in a throwaway temp dir — NEVER touches production memory/*.db.
"""

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, ".")

TMP = Path(tempfile.mkdtemp(prefix="aicos_test_"))
DB = lambda name: str(TMP / name)
TEST_DB = DB("scheduler.db")
errors, passed, total = [], 0, 0


class TestBlock:
    def __init__(self, name):
        self.name = name
    def __enter__(self):
        global total; total += 1; return self
    def __exit__(self, et, ev, tb):
        global passed
        if et is None:
            passed += 1; print(f"  [OK] {self.name}")
        else:
            errors.append((self.name, str(ev))); print(f"  [FAIL] {self.name}: {ev}")
        return True

def test(name):
    return TestBlock(name)


class MockLLM:
    """Simulates ClaudeLLM. `tool_calls`: list of (name, input) to fire before replying."""
    def __init__(self, reply="oke bro", tool_calls=None):
        self.reply = reply
        self.tool_calls = tool_calls or []
        self.ran = []

    def chat(self, messages, max_tokens=4096):
        return self.reply

    def stream_with_tools(self, messages, tools, runner, max_tokens=4096):
        for name, inp in self.tool_calls:
            self.ran.append((name, runner(name, {"input": inp})))
        for c in self.reply:
            yield c


print("=" * 60)
print("AI CHIEF OF STAFF — TEST SUITE")
print("=" * 60)

print("\n>>> 1. Core")

with test("extract_json — object, array, noisy, invalid"):
    from app.schema import extract_json
    assert extract_json('{"a":1}') == {"a": 1}
    assert extract_json('prefix {"a":1} end') == {"a": 1}
    assert extract_json('[{"subject":"R"}]') == [{"subject": "R"}]
    assert extract_json('not json') is None

with test("Profile"):
    from app.agent.profile import Profile
    assert "Rendy" in Profile().load()

print("\n>>> 2. Memory")

from app.agent.memory import Memory

with test("Short-term memory"):
    m = Memory(DB("conversations.db"))
    m.add("u1", "user", "hi"); m.add("u1", "assistant", "yo")
    assert len(m.get("u1")) == 2

with test("Long-term memory — keyword recall on paraphrased query"):
    from app.memory.long_term import LongTermMemory
    lt = LongTermMemory(DB("long_term.db"))
    lt.add("u1", "gimana cara deploy bot telegram ke vps", "pake systemd bro")
    hits = lt.search("u1", "deploy vps gimana ya")
    assert hits and "systemd" in hits[0]["assistant"]
    assert lt.search("u1", "resep nasi goreng") == []
    assert lt.search("u2", "deploy vps") == []  # other user's memories hidden

with test("Knowledge graph upsert + tokenized query"):
    from app.os.knowledge_graph import KnowledgeGraph
    kg = KnowledgeGraph(DB("knowledge.db"))
    kg.upsert("u1", "Rendy", "works_at", "PT X", 1.0)
    assert kg.about("u1", "Rendy")
    assert "Rendy" in kg.context_for("u1", "dimana rendy kerja works")

with test("Knowledge graph cleanup drops low-confidence facts"):
    kg.upsert("u1", "Rendy", "likes", "kopi", 1.0)
    kg._db.commit_sql("UPDATE facts SET confidence = 0.1 WHERE object = 'kopi'")
    kg.cleanup()
    assert not any(f["object"] == "kopi" for f in kg.about("u1", "Rendy"))

print("\n>>> 3. Agent (native tool-use)")

with test("Agent — plain chat streams persona reply"):
    from app.agent.agent import Agent
    a = Agent(MockLLM(reply="Halo bro!"), Memory(DB("c1.db")))
    out = "".join(a.chat_stream("u1", "halo"))
    assert out == "Halo bro!"

with test("Agent — cached static block + dynamic tail"):
    a = Agent(MockLLM(), Memory(DB("c2.db")))
    blocks = a._system_blocks("u1", "halo")
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "Rendy" in blocks[0]["text"]          # persona + profile cached
    assert "HARI INI" in blocks[1]["text"]       # date stays uncached

with test("Agent — tool call routed through runner"):
    smart = MockLLM(reply="jam 3 sore bro", tool_calls=[("time", "")])
    a = Agent(smart, Memory(DB("c3.db")))
    out = "".join(a.chat_stream("u1", "jam berapa?"))
    assert "jam 3" in out
    assert smart.ran and smart.ran[0][0] == "time"  # runner actually executed 'time'

with test("Agent — remember tool writes to knowledge graph"):
    from app.os.knowledge_graph import KnowledgeGraph
    kg2 = KnowledgeGraph(DB("knowledge2.db"))
    smart = MockLLM(reply="oke gue inget", tool_calls=[("remember", "Rendy|building|AI CoS")])
    a = Agent(smart, Memory(DB("c4.db")), knowledge_graph=kg2)
    "".join(a.chat_stream("u1", "gue lagi bangun AI CoS"))
    assert any(f["object"] == "AI CoS" for f in kg2.about("u1", "Rendy"))

with test("Agent — media markers survive to output, stripped from tool_result"):
    smart = MockLLM(reply="nih videonya", tool_calls=[("cctv", "view:malioboro")])
    a = Agent(smart, Memory(DB("c5.db")))
    a.tools["cctv"] = type("T", (), {
        "run": lambda self, i, user_id="": "Camera Malioboro [VIDEO:/tmp/x.mp4]"
    })()
    out = "".join(a.chat_stream("u1", "cctv malioboro"))
    assert "[VIDEO:/tmp/x.mp4]" in out          # marker forwarded for media send
    assert "[VIDEO" not in smart.ran[0][1]        # stripped before going to model

with test("Agent — phrase() returns in-persona line via llm.chat"):
    a = Agent(MockLLM(reply="woy meeting 5 menit lagi tuh"), Memory(DB("c6.db")))
    assert "meeting" in a.phrase("Reminder 'meeting' due sekarang.")

with test("Agent — crash falls back gracefully"):
    class Crash:
        def stream_with_tools(self, *a, **k): raise Exception("down")
    out = "".join(Agent(Crash(), Memory(DB("c7.db"))).chat_stream("u1", "hi"))
    assert "error" in out.lower()

print("\n>>> 4. Scheduler")

from app.agent.scheduler import Scheduler

with test("Scheduler delay + fire"):
    calls = []
    s = Scheduler(on_notify=lambda uid, msg: calls.append(msg), db_path=TEST_DB)
    s.add("u1", "ping", delay_seconds=1); s.start(); time.sleep(3); s.stop()
    assert calls and calls[0] == "ping"

with test("Scheduler due_today + has_pending"):
    s = Scheduler(db_path=DB("sched2.db"))
    s.add("u1", "meeting sore")  # due now — always today, even near midnight
    assert "meeting sore" in s.due_today("u1")
    assert s.due_today("u2") == []
    assert s.has_pending("meeting sore") and not s.has_pending("__morning_brief__")

print("\n>>> 5. Proactive")

with test("send_proactive stores assistant turn in memory"):
    from app.interfaces.telegram import TelegramBot
    mem = Memory(DB("c8.db"))
    a = Agent(MockLLM(reply="gas, jangan lupa standup"), mem)
    bot = TelegramBot(a, mem, Scheduler(db_path=DB("sched3.db")))
    sent = []
    bot._send_to_user = sent.append
    bot._on_scheduled("507090539", "standup")
    assert sent and "standup" in sent[0]
    hist = mem.get("507090539")
    assert hist and hist[-1]["role"] == "assistant" and "standup" in hist[-1]["content"]

with test("morning brief routed through scheduler.morning_brief"):
    mem = Memory(DB("c9.db"))
    sched = Scheduler(db_path=DB("sched4.db"))
    sched.morning_brief = lambda: "pagi bro, cerah hari ini"
    bot = TelegramBot(Agent(MockLLM(), mem), mem, sched)
    sent = []
    bot._send_to_user = sent.append
    bot._on_scheduled("system", "__morning_brief__")
    assert sent == ["pagi bro, cerah hari ini"]

with test("nudge gate — max 2/day, 4h apart"):
    from datetime import datetime, timedelta, timezone
    from app.app import _nudge_allowed
    from app.os.knowledge_graph import KnowledgeGraph
    kg3 = KnowledgeGraph(DB("knowledge3.db"))
    now = datetime.now(timezone(timedelta(hours=7)))
    assert _nudge_allowed(kg3, now)
    kg3.upsert("system", "Rendy", "nudged_on", (now - timedelta(hours=1)).isoformat(), 1.0)
    assert not _nudge_allowed(kg3, now)  # too soon (<4h)
    kg3._db.commit_sql("DELETE FROM facts")
    kg3.upsert("system", "Rendy", "nudged_on", (now - timedelta(hours=5)).isoformat(), 1.0)
    assert _nudge_allowed(kg3, now)      # 1 nudge, >4h ago
    kg3.upsert("system", "Rendy", "nudged_on", (now - timedelta(hours=9)).isoformat(), 1.0)
    assert not _nudge_allowed(kg3, now)  # already 2 today

print("\n>>> 6. Tools")

with test("Time tool"):
    from app.tools.time_tool import TimeTool
    assert "2026" in TimeTool().run("")

with test("Tool factory + native schema shape"):
    from app.tools.factory import load_tools
    tools = load_tools(scheduler=None)
    assert "time" in tools and "weather" in tools
    assert all(t.name and t.description for t in tools.values())

with test("Reminder parse"):
    from app.tools.reminder_tool import ReminderTool
    r = ReminderTool(Scheduler(db_path=TEST_DB))
    assert "Error" not in r.run("delay:60:test", user_id="u1")
    assert "Error" in r.run("garbage", user_id="u1")

print("\n>>> 7. LLM plumbing")

with test("ClaudeLLM _split separates system (incl. block list)"):
    from app.llm.anthropic import ClaudeLLM
    llm = ClaudeLLM.__new__(ClaudeLLM); llm.model = "claude-sonnet-5"
    blocks = [{"type": "text", "text": "S", "cache_control": {"type": "ephemeral"}}]
    system, chat = llm._split([
        {"role": "system", "content": blocks},
        {"role": "user", "content": "hi"},
    ])
    assert system == blocks and chat == [{"role": "user", "content": "hi"}]

print("\n>>> 8. Watcher")

with test("Watcher register + fire calls on_alert"):
    from app.agent.watcher import WatcherManager
    alerts = []
    wm = WatcherManager(on_alert=alerts.append)
    wm.register(lambda: "alert!", 1); wm.start(); time.sleep(2)
    assert alerts

print(f"\n{'=' * 60}")
print(f"RESULTS: {passed}/{total} passed", end="")
if errors:
    print(f", {len(errors)} failed:")
    for n, e in errors:
        print(f"  - {n}: {e}")
else:
    print(" — ALL PASSED")
print("=" * 60)
sys.exit(1 if errors else 0)
