"""Full-stack test suite for AI Chief of Staff. Run: python3 tests/test_all.py"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")

# Force clean state
TEST_DB = "memory/_test_scheduler.db"
for f in ["memory/conversations.db", "memory/goals.db", "memory/knowledge.db",
           "memory/scheduler.db", "memory/long_term.db", TEST_DB]:
    Path(f).unlink(missing_ok=True)

errors = []
passed = 0
total = 0


class TestBlock:
    def __init__(self, name):
        self.name = name
    def __enter__(self):
        global total
        total += 1
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            global passed
            passed += 1
            print(f"  [OK] {self.name}")
        else:
            errors.append((self.name, str(exc_val)))
            print(f"  [FAIL] {self.name}: {exc_val}")
        return True  # suppress exception

def test(name):
    return TestBlock(name)


# Mock LLM — returns JSON or text based on call count
class MockLLM:
    def __init__(self, responses=None):
        self.responses = responses or []
        self.calls = []
        self.idx = 0

    def chat(self, messages, max_tokens=4096):
        self.calls.append(messages)
        if self.idx < len(self.responses):
            resp = self.responses[self.idx]
            self.idx += 1
            return resp
        return '{"action": "chat", "message": "ok"}'

    def stream(self, messages, max_tokens=4096):
        text = self.chat(messages, max_tokens)
        for char in text:
            yield char


print("=" * 60)
print("AI CHIEF OF STAFF — FULL TEST SUITE")
print("=" * 60)

# ─── 1. Core Infrastructure ───
print("\n>>> 1. Core Infrastructure")

with test("EventBus pub/sub"):
    from app.os.event_bus import EventBus
    bus = EventBus()
    results = []
    bus.on("test", lambda p, b: results.append(p["msg"]))
    bus.emit("test", {"msg": "hello"})
    time.sleep(0.2)
    assert results == ["hello"]

with test("EventBus priority ordering"):
    bus2 = EventBus()
    order = []
    bus2.on("x", lambda p, b: order.append(1), priority=1)
    bus2.on("x", lambda p, b: order.append(0), priority=0)
    bus2.emit("x", {})
    time.sleep(0.2)
    assert order == [0, 1]

with test("EventBus error isolation"):
    bus3 = EventBus()
    bus3.on("x", lambda p, b: (_ for _ in ()).throw(Exception("boom")))
    results2 = []
    bus3.on("x", lambda p, b: results2.append("ok"))
    bus3.emit("x", {})
    time.sleep(0.2)
    assert results2 == ["ok"]

with test("Database CRUD"):
    from app.lib.database import Database
    db = Database("memory/test_db.db")
    db.commit_sql("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, val TEXT)")
    rid = db.insert("INSERT INTO t (val) VALUES (?)", ("test",))
    assert rid > 0
    rows = db.fetch("SELECT val FROM t WHERE id=?", (rid,))
    assert rows[0][0] == "test"
    db.commit_sql("DROP TABLE t")

with test("Schema validation"):
    from app.schema import validate, extract_json
    assert validate({"action": "chat", "message": "hi"}, lambda n: True) is None
    assert validate({"action": "tool", "tool": "time"}, lambda n: n == "time") is None
    assert validate({"action": "chain", "steps": [{"tool": "time", "input": ""}]}, lambda n: True) is None
    assert validate({"action": "chat"}, lambda n: True) is not None
    assert validate({"action": "unknown"}, lambda n: True) is not None
    assert extract_json('{"action":"chat","message":"hi"}') == {"action": "chat", "message": "hi"}
    assert extract_json('prefix {"a":1} suffix') == {"a": 1}
    assert extract_json('not json') is None

with test("Verdict schema"):
    from app.schema import validate_verdict
    assert validate_verdict({"verdict": "good", "feedback": "ok"}) is None
    assert validate_verdict({"verdict": "retry", "feedback": "less"}) is None
    assert validate_verdict({"verdict": "bad"}) is not None

with test("GuarPermission check"):
    from app.os.guard import Guard
    Path("config").mkdir(exist_ok=True)
    Path("config/permissions.json").write_text(json.dumps({
        "rules": [
            {"action": "tool.execute", "tool": "auto_apply", "require_approval": True},
            {"action": "tool.execute", "tool": "files.delete", "require_approval": True},
            {"action": "tool.execute", "tool": "*", "require_approval": False},
        ]
    }))
    g = Guard()
    assert g.check("tool.execute", "auto_apply") == True
    assert g.check("tool.execute", "time") == False
    assert g.check("tool.execute", "files.delete") == True
    safe, flags = g.validate("Halo!")
    assert safe == True

with test("Profile"):
    from app.agent.profile import Profile
    p = Profile()
    text = p.load()
    assert "Rendy" in text
    contact = p.contact()
    assert contact.get("full_name") == "Rendy Andika"
    assert "React.js" in p.raw()["skills"]

with test("Prompt builder"):
    from app.prompt.builder import PromptBuilder
    pb = PromptBuilder()
    msgs = pb.build("system", "profile text", [], "hello")
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    assert msgs[1]["content"] == "hello"

# ─── 2. Memory System ───
print("\n>>> 2. Memory System")

with test("Short-term memory"):
    from app.agent.memory import Memory
    m = Memory()
    m.add("u1", "user", "hello")
    m.add("u1", "assistant", "hi")
    h = m.get("u1")
    assert len(h) == 2
    assert h[0]["role"] == "user"

with test("Long-term memory search"):
    from app.memory.long_term import LongTermMemory
    ltm = LongTermMemory()
    ltm.add("u1", "siapa nama gue?", "Nama lo Rendy.")
    ltm.add("u1", "cuaca gimana?", "Cerah.")
    results = ltm.search("u1", "nama")
    assert len(results) >= 1

with test("Knowledge graph upsert + query"):
    from app.os.knowledge_graph import KnowledgeGraph
    kg = KnowledgeGraph()
    kg.upsert("u1", "Rendy", "works_at", "PT X", 1.0)
    kg.upsert("u1", "Rendy", "has_skill", "Python", 0.7)
    kg.upsert("u1", "Rendy", "has_skill", "Python", 0.5)  # confidence bump
    facts = kg.about("u1", "Rendy")
    assert len(facts) == 2
    ctx = kg.context_for("u1", "works")
    assert "Rendy" in ctx

with test("Knowledge graph cleanup"):
    kg2 = KnowledgeGraph()
    kg2.upsert("u1", "Rendy", "old_fact", "stale", 0.3)
    # Decay + cleanup
    kg2.cleanup()
    # After cleanup, low-confidence old facts should be removed or decayed

with test("Goal manager CRUD"):
    from app.os.goal_manager import GoalManager
    gm = GoalManager()
    gid = gm.create_goal("u1", "Dapet remote job")
    assert gid > 0
    pid = gm.create_project(gid, "Job Hunting")
    assert pid > 0
    tid = gm.create_task(pid, "Update CV", "desc", "tool:input")
    assert tid > 0
    goals = gm.list_goals("u1")
    assert len(goals) == 1
    next_task = gm.next_task(gid)
    assert next_task is not None
    summary = gm.summary("u1")
    assert "remote job" in summary.lower()

# ─── 3. Agent Architecture ───
print("\n>>> 3. Agent Architecture")

with test("Planner — chat action"):
    from app.agent.agent import Planner, Executor, Reflector, Agent
    from app.agent.profile import Profile
    from app.agent.memory import Memory

    class FakeRegistry:
        def get(self, n): return "time" if n == "time" else None
        def describe(self): return [{"name": "time", "description": "Get current time"}]

    mock = MockLLM(['{"action": "chat", "message": "Halo Rendy!"}'])
    planner = Planner(mock, Profile(), FakeRegistry())
    result = planner.plan("halo", [])
    assert result == {"action": "chat", "message": "Halo Rendy!"}

with test("Planner — tool action"):
    mock2 = MockLLM(['{"action": "tool", "tool": "time", "input": ""}'])
    planner2 = Planner(mock2, Profile(), FakeRegistry())
    result2 = planner2.plan("jam berapa?", [])
    assert result2 == {"action": "tool", "tool": "time", "input": ""}

with test("Planner — natural language fallback"):
    mock3 = MockLLM(["Gue nggak bisa jawab itu bro, privasi."])
    planner3 = Planner(mock3, Profile(), FakeRegistry())
    result3 = planner3.plan("data pribadi?", [])
    assert result3 == {"action": "chat", "message": "Gue nggak bisa jawab itu bro, privasi."}

with test("Executor — normal response"):
    mock4 = MockLLM(['{"action": "chat", "message": "Sekarang jam 3 sore, bro!"}'])
    executor = Executor(mock4, Profile())
    result4 = executor.respond("jam berapa?", [], "[time] 15:00")
    assert result4 == "Sekarang jam 3 sore, bro!"

with test("Reflector — good verdict"):
    mock5 = MockLLM(['{"verdict": "good", "feedback": "lengkap"}'])
    reflector = Reflector(mock5)
    v = reflector.reflect("jam?", "Sekarang jam 3.", "")
    assert v["verdict"] == "good"

with test("Reflector — retry verdict"):
    mock6 = MockLLM(['{"verdict": "retry", "feedback": "kurang spesifik"}'])
    reflector2 = Reflector(mock6)
    v2 = reflector2.reflect("jam?", "Udah sore.", "")
    assert v2["verdict"] == "retry"

with test("Agent — full chat flow"):
    mock7 = MockLLM([
        '{"action": "chat", "message": "Halo bro!"}',
        '{"verdict": "good", "feedback": "ok"}'
    ])
    memory = Memory()
    memory.add("u1", "user", "halo")
    agent = Agent(mock7, memory)
    resp = agent.chat("u1", "halo")
    assert "Halo" in resp or "bro" in resp

with test("Agent — chat_stream flow"):
    mock8 = MockLLM(['{"action": "chat", "message": "OK bro"}'])
    mem = Memory()
    mem.add("u1", "user", "test")
    agent8 = Agent(mock8, mem)
    tokens = list(agent8.chat_stream("u1", "halo"))
    assert len(tokens) > 0
    assert "".join(tokens) == "OK bro"

with test("Agent — visual tool skip"):
    mock9 = MockLLM([
        '{"action": "tool", "tool": "cctv", "input": "view:1"}',
        '{"verdict": "good", "feedback": "ok"}'
    ])
    mem9 = Memory()
    mem9.add("u1", "user", "test")
    agent9 = Agent(mock9, mem9)
    resp9 = agent9.chat("u1", "cctv 1")
    assert isinstance(resp9, str)

# ─── 4. Scheduler ───
print("\n>>> 4. Scheduler")

with test("Scheduler delay + fire"):
    from app.agent.scheduler import Scheduler
    calls = []
    s = Scheduler(on_notify=lambda uid, msg: calls.append(msg), db_path=TEST_DB)
    s.add("u1", "test reminder", delay_seconds=1)
    s.start()
    time.sleep(3)
    s.stop()
    assert len(calls) >= 1
    assert calls[0] == "test reminder"

with test("Scheduler recurring"):
    calls2 = []
    s2 = Scheduler(on_notify=lambda uid, msg: calls2.append(msg))
    s2.add("u1", "recurring", interval_seconds=1)
    s2.start()
    time.sleep(3)
    s2.stop()
    assert len(calls2) >= 2

with test("Scheduler calc_daily"):
    from app.agent.scheduler import Scheduler
    run_at, interval = Scheduler.calc_daily("09:00")
    assert interval == 86400
    assert "09:00" in run_at

with test("Scheduler calc_weekly"):
    run_at, interval = Scheduler.calc_weekly("monday", "08:00")
    assert interval == 604800
    assert "08:00" in run_at

# ─── 5. Tools ───
print("\n>>> 5. Tools")

with test("Time tool"):
    from app.tools.time_tool import TimeTool
    t = TimeTool()
    assert "2026" in t.run("")

with test("Calculator tool"):
    from app.tools.calc_tool import CalculatorTool
    c = CalculatorTool()
    assert "14" in c.run("2+3*4")
    assert "3.14" in c.run("round(pi, 2)")

with test("HTTP tool parse"):
    from app.tools.http_tool import HttpTool
    h = HttpTool()
    # Unknown method caught
    r = h.run("delete:https://x.com")
    assert "Error" in r or "unknown" in r.lower()

with test("File tool sandbox"):
    from app.tools.file_tool import FileTool
    f = FileTool()
    r = f.run("write:test.txt:hello")
    assert "Written" in r
    r = f.run("read:test.txt")
    assert "hello" in r
    r = f.run("delete:test.txt")
    assert "Deleted" in r
    r = f.run("read:../../../etc/passwd")
    assert "Error" in r

with test("Reminder parse + format"):
    from app.agent.scheduler import Scheduler
    from app.tools.reminder_tool import ReminderTool
    s = Scheduler(db_path=TEST_DB)
    r = ReminderTool(s)
    out = r.run("delay:60:test", user_id="u1")
    assert "Error" not in out
    out = r.run("at:2026-12-25T08:00:00:test", user_id="u1")
    assert "Error" not in out
    out = r.run("daily:09:00:test", user_id="u1")
    assert "Error" not in out
    out = r.run("weekly:monday:08:00:test", user_id="u1")
    assert "Error" not in out

with test("Weather parse"):
    from app.tools.weather_tool import WeatherTool
    w = WeatherTool()
    # Geocode fallback
    result = w.run("Malioboro, Yogyakarta")
    assert "Error" not in result

with test("CCTV list"):
    from app.tools.cctv_tool import CctvTool
    cctv = CctvTool()
    result = cctv.run("list:malioboro")
    assert "Malioboro" in result or "Tidak ada" in result

with test("CCTV info"):
    result = cctv.run("info:1")
    assert "Camera" in result

with test("Job hunt search"):
    from app.tools.job_hunt_tool import JobHuntTool
    j = JobHuntTool()
    result = j.run("search:frontend engineer|jakarta")
    assert "linkedin" in result.lower() or "glints" in result.lower()

with test("Auto apply resolve"):
    from app.tools.auto_apply_tool import AutoApplyTool
    from app.agent.profile import Profile
    a = AutoApplyTool(profile=Profile())
    assert a._resolve_value("email", Profile().contact(), Profile().raw()) == "rendyndika@gmail.com"
    assert "React" in a._resolve_value("__skills__", {}, Profile().raw())

with test("Tool registry"):
    from app.tools.init import load_tools
    from app.agent.scheduler import Scheduler
    tools = load_tools(scheduler=Scheduler())
    names = tools.list()
    assert "time" in names
    assert "weather" in names
    assert "cctv" in names

# ─── 6. Edge Cases ───
print("\n>>> 6. Edge Cases")

with test("Planner with empty messages"):
    mock_empty = MockLLM([None])  # returns None
    try:
        mock_empty.chat([])
    except:
        pass

with test("Agent fallback on planner crash"):
    class CrashLLM:
        def chat(self, m): raise Exception("API down")
    agent_crash = Agent(CrashLLM(), Memory())
    resp = agent_crash.chat("u1", "halo")
    assert "error" in resp.lower() or "kesulitan" in resp.lower()

with test("Agent with long message"):
    long_msg = "test " * 200
    mock_long = MockLLM(['{"action": "chat", "message": "ok"}'])
    mem_long = Memory()
    mem_long.add("u1", "user", "prev msg")
    agent_long = Agent(mock_long, mem_long)
    resp = agent_long.chat("u1", long_msg)
    assert resp is not None

with test("Knowledge graph empty query"):
    kg = KnowledgeGraph()
    ctx = kg.context_for("u1", "asdfghjkl")
    assert ctx == ""

with test("Scheduler no callback"):
    s = Scheduler(db_path=TEST_DB)
    s.add("u1", "test", delay_seconds=1)
    s.start()
    time.sleep(2)
    s.stop()
    # Should not crash

with test("Goal manager empty state"):
    gm = GoalManager()
    assert gm.summary("unknown") == "Belum ada goal aktif."

with test("File tool non-existent read"):
    from app.tools.file_tool import FileTool
    f = FileTool()
    assert "not found" in f.run("read:nonexistent.txt").lower()

with test("Calculator invalid expression"):
    from app.tools.calc_tool import CalculatorTool
    c = CalculatorTool()
    assert "Error" in c.run("1/0")

with test("Reminder invalid format"):
    from app.agent.scheduler import Scheduler
    from app.tools.reminder_tool import ReminderTool
    r = ReminderTool(Scheduler(db_path=TEST_DB))
    assert "Error" in r.run("invalid", user_id="u1")
    assert "Error" in r.run("delay:abc:test", user_id="u1")

# ─── 7. Stream Test ───
print("\n>>> 7. Streaming")

with test("ClaudeLLM stream (mock)"):
    from app.llm.anthropic import ClaudeLLM
    # Test build_kwargs
    llm = ClaudeLLM()
    kwargs = llm._build_kwargs([{"role": "user", "content": "hi"}], 100)
    assert kwargs["model"] is not None
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]
    assert kwargs["thinking"] == {"type": "disabled"}

with test("Agent stream fallback"):
    class CrashStreamLLM:
        def chat(self, m): raise Exception("stream crash")
        def stream(self, m, max_tokens=4096):
            raise Exception("stream crash")
    agent_s = Agent(CrashStreamLLM(), Memory())
    tokens = list(agent_s.chat_stream("u1", "test"))
    assert len(tokens) > 0

with test("Agent stream — tool action"):
    mock_stream = MockLLM([
        '{"action": "tool", "tool": "time", "input": ""}',
        '{"verdict": "good", "feedback": "ok"}'
    ])
    agent_st = Agent(mock_stream, Memory())
    tokens = list(agent_st.chat_stream("u1", "jam?"))
    assert len(tokens) > 0

# ─── 8. Wather Manager ───
print("\n>>> 8. Watcher Manager")

with test("Watcher register + fire"):
    from app.agents.watcher import WatcherManager
    bus = EventBus()
    alerts = []
    bus.on("watcher.alert", lambda p, b: alerts.append(p["message"]))
    wm = WatcherManager(bus)
    wm.register(lambda: "test alert", 1)
    time.sleep(2)
    assert len(alerts) >= 1

# ─── Summary ───
print(f"\n{'=' * 60}")
print(f"RESULTS: {passed}/{total} passed", end="")
if errors:
    print(f", {len(errors)} failed:")
    for name, err in errors:
        print(f"  - {name}: {err}")
else:
    print(" — ALL PASSED")
print(f"{'=' * 60}")
sys.exit(1 if errors else 0)
