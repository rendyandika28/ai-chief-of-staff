"""Test suite for AI Chief of Staff (native tool-use architecture).
Run: python3 tests/test_all.py"""

import sys
import time
from pathlib import Path

sys.path.insert(0, ".")

for f in ["memory/conversations.db", "memory/knowledge.db",
          "memory/scheduler.db", "memory/long_term.db", "memory/_test_scheduler.db"]:
    Path(f).unlink(missing_ok=True)

TEST_DB = "memory/_test_scheduler.db"
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

    def stream(self, messages, max_tokens=4096):
        for c in self.reply:
            yield c

    def stream_with_tools(self, messages, tools, runner, max_tokens=1024):
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

with test("EventBus pub/sub"):
    from app.os.event_bus import EventBus
    bus = EventBus(); got = []
    bus.on("t", lambda p, b: got.append(p["m"]))
    bus.emit("t", {"m": "hi"}); time.sleep(0.2)
    assert got == ["hi"]

with test("Profile"):
    from app.agent.profile import Profile
    assert "Rendy" in Profile().load()

with test("PromptBuilder"):
    from app.prompt.builder import PromptBuilder
    msgs = PromptBuilder().build("sys", "prof", [], "hello")
    assert msgs[0]["role"] == "system" and msgs[-1]["content"] == "hello"

print("\n>>> 2. Memory")

with test("Short-term memory"):
    from app.agent.memory import Memory
    m = Memory(); m.add("u1", "user", "hi"); m.add("u1", "assistant", "yo")
    assert len(m.get("u1")) == 2

with test("Knowledge graph upsert + query"):
    from app.os.knowledge_graph import KnowledgeGraph
    kg = KnowledgeGraph()
    kg.upsert("u1", "Rendy", "works_at", "PT X", 1.0)
    assert kg.about("u1", "Rendy")
    assert "Rendy" in kg.context_for("u1", "works")

print("\n>>> 3. Agent (native tool-use)")

from app.agent.memory import Memory

with test("Agent — plain chat streams persona reply"):
    from app.agent.agent import Agent
    a = Agent(MockLLM(), MockLLM(reply="Halo bro!"), Memory())
    out = "".join(a.chat_stream("u1", "halo"))
    assert out == "Halo bro!"

with test("Agent — tool call routed through runner"):
    smart = MockLLM(reply="jam 3 sore bro", tool_calls=[("time", "")])
    a = Agent(MockLLM(), smart, Memory())
    out = "".join(a.chat_stream("u1", "jam berapa?"))
    assert "jam 3" in out
    assert smart.ran and smart.ran[0][0] == "time"  # runner actually executed 'time'

with test("Agent — media markers survive to output, stripped from tool_result"):
    smart = MockLLM(reply="nih videonya", tool_calls=[("cctv", "view:malioboro")])
    a = Agent(MockLLM(), smart, Memory())
    a.tools.tools["cctv"] = type("T", (), {
        "run": lambda self, i, user_id="": "Camera Malioboro [VIDEO:/tmp/x.mp4]"
    })()
    out = "".join(a.chat_stream("u1", "cctv malioboro"))
    assert "[VIDEO:/tmp/x.mp4]" in out          # marker forwarded for media send
    assert "[VIDEO" not in smart.ran[0][1]        # stripped before going to model

with test("Agent — crash falls back gracefully"):
    class Crash:
        def stream_with_tools(self, *a, **k): raise Exception("down")
    out = "".join(Agent(MockLLM(), Crash(), Memory()).chat_stream("u1", "hi"))
    assert "error" in out.lower()

print("\n>>> 4. Scheduler")

with test("Scheduler delay + fire"):
    from app.agent.scheduler import Scheduler
    calls = []
    s = Scheduler(on_notify=lambda uid, msg: calls.append(msg), db_path=TEST_DB)
    s.add("u1", "ping", delay_seconds=1); s.start(); time.sleep(3); s.stop()
    assert calls and calls[0] == "ping"

print("\n>>> 5. Tools")

with test("Time tool"):
    from app.tools.time_tool import TimeTool
    assert "2026" in TimeTool().run("")

with test("Tool registry + native schema shape"):
    from app.tools.init import load_tools
    tools = load_tools(scheduler=None)
    assert "time" in tools.list() and "weather" in tools.list()
    d = tools.describe()
    assert all("name" in t and "description" in t for t in d)

with test("Reminder parse"):
    from app.agent.scheduler import Scheduler
    from app.tools.reminder_tool import ReminderTool
    r = ReminderTool(Scheduler(db_path=TEST_DB))
    assert "Error" not in r.run("delay:60:test", user_id="u1")
    assert "Error" in r.run("garbage", user_id="u1")

print("\n>>> 6. LLM plumbing")

with test("ClaudeLLM _split separates system"):
    from app.llm.anthropic import ClaudeLLM
    llm = ClaudeLLM.__new__(ClaudeLLM); llm.model = "claude-sonnet-5"
    system, chat = llm._split([
        {"role": "system", "content": "S"},
        {"role": "user", "content": "hi"},
    ])
    assert system == "S" and chat == [{"role": "user", "content": "hi"}]

print("\n>>> 7. Watcher")

with test("Watcher register + fire emits alert"):
    from app.agents.watcher import WatcherManager
    bus = EventBus(); alerts = []
    bus.on("watcher.alert", lambda p, b: alerts.append(p["message"]))
    wm = WatcherManager(bus)
    wm.register(lambda: "alert!", 1); time.sleep(2)
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
