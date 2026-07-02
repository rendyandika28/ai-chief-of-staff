# Phase 7A — Core OS Layer Design

**Date:** 2026-07-01  
**Status:** Approved  
**Sub-project:** 7A of 4 (Core OS Layer)  
**Dependency for:** 7B (Goal Engine), 7C (Knowledge Graph), 7D (Agent Swarm)

---

## Overview

Phase 7A builds the operating system foundation: event bus, plugin system, multi-LLM router, permission layer, and safety layer. These four modules form the backbone that all future phases build on. Every agent, tool, and plugin communicates through the event bus. Nothing is hard-wired.

---

## 1. Architecture

```
┌─────────────────────────────────────────────┐
│                  Event Bus                    │
│         (pub/sub, in-process, async)          │
├──────────┬──────────┬──────────┬─────────────┤
│ Plugin   │  Multi-  │Permission│   Safety    │
│ System   │   LLM    │  Layer   │   Layer     │
│ (loader) │  Router  │ (Guard)  │  (Guard)    │
├──────────┴──────────┴──────────┴─────────────┤
│              Existing Agent Core              │
│     Planner | Executor | Reflector | Tools    │
└─────────────────────────────────────────────┘
```

No module directly imports another module's internals. All communication flows through the EventBus.

---

## 2. Folder Structure

```
app/
├── os/                          # NEW
│   ├── __init__.py
│   ├── event_bus.py             # EventBus class
│   ├── plugin.py                # @tool, @agent, @on decorators + PluginLoader
│   ├── llm_router.py            # LLMRouter + ModelConfig + TaskProfile
│   └── guard.py                 # Guard — permission check + safety validation
│
├── plugins/                     # NEW — auto-discovered plugin directory
│   └── README.md
│
├── config/
│   ├── models.json              # NEW — model configurations
│   ├── permissions.json         # NEW — permission rules matrix
│   └── plugins.json             # NEW — plugin enable/disable list
│
├── agent/   (existing, unchanged)
├── tools/   (existing, unchanged)
├── llm/     (existing, unchanged)
├── memory/  (existing, unchanged)
├── interfaces/ (existing, unchanged)
├── lib/     (existing, unchanged)
├── schema.py (existing, unchanged)
└── app.py   (updated — init EventBus, PluginLoader, Guard)
```

---

## 3. Event Bus (`app/os/event_bus.py`)

### Interface

```python
class EventBus:
    def emit(event_type: str, payload: dict, user_id: str = "")
    def on(event_type: str, handler: callable, priority: int = 0)
    def once(event_type: str, handler: callable)
    def off(event_type: str, handler: callable)
    async def flush()
```

### Behavior

- In-process pub/sub (no external dependencies).
- Handlers are async. Execution is parallel per event (asyncio.gather).
- `priority`: lower number = earlier execution. Guard must run before tool execution.
- `flush()`: awaits all pending handlers. Used during shutdown.
- `once()`: handler auto-removes after one invocation.
- Errors in one handler do NOT break other handlers. Errors are caught and logged, then emitted as `error.occurred`.

### Built-in Event Types

| Event | Payload | Consumers |
|---|---|---|
| `message.received` | `{text, user_id}` | Planner, Logger |
| `tool.before_execute` | `{tool_name, input, user_id}` | Guard (permission) |
| `tool.after_execute` | `{tool_name, result, user_id}` | Logger, Memory |
| `response.ready` | `{response, user_id, tool_results}` | Reflector, Logger |
| `reminder.due` | `{user_id, message}` | Notification adapter |
| `error.occurred` | `{source, error, context}` | Logger, Monitor |

---

## 4. Plugin System (`app/os/plugin.py`)

### Decorators

```python
@tool(name="weather", description="Get current weather")
class WeatherTool(Tool):
    def run(self, input, user_id=""): ...

@on("message.received", priority=10)
async def log_message(event, bus): ...
```

### Auto-discovery

On startup, `PluginLoader.discover("app/plugins/")` scans for `__init__.py` files containing decorators. Decorators register handlers into the `ToolRegistry` and `EventBus` automatically. No manual wiring.

### Plugin Config (`config/plugins.json`)

```json
{
  "enabled": ["weather_plugin", "job_hunt_plugin"],
  "disabled": ["cctv_plugin"]
}
```

Disabled plugins are skipped during discovery. Code stays in the repo but handlers are not registered.

### Migration Path

Existing 12 tools continue to work via manual `ToolRegistry.register()`. Tools are migrated to `app/plugins/` incrementally. Both paths coexist — the decorator registers into the same `ToolRegistry` instance.

---

## 5. Multi-LLM Router (`app/os/llm_router.py`)

### Interface

```python
class LLMRouter:
    def __init__(self, models: list[ModelConfig])
    def route(self, task: TaskProfile) -> ModelConfig

@dataclass
class TaskProfile:
    task_type: str       # "chat", "tool_selection", "reflection", "summarize"
    max_tokens: int
    latency_ms: int
    priority: str        # "cost" | "speed" | "quality"

@dataclass
class ModelConfig:
    id: str
    provider: str
    capabilities: list[str]
    cost_per_1k: float
    avg_latency_ms: int
    max_tokens: int
    default: bool = False
```

### Routing Logic

1. Filter models by `capabilities` matching `task_type`.
2. Sort by `priority`: cost → cheapest first, speed → lowest latency first, quality → highest max_tokens first.
3. Return first match. Fallback to `default: true` model if no match.
4. Record selection in event log (`model.selected` event).

### Model Config (`config/models.json`)

```json
{
  "models": [
    {
      "id": "deepseek-chat",
      "provider": "deepseek",
      "capabilities": ["chat", "tool_selection", "reflection"],
      "cost_per_1k": 0.00014,
      "avg_latency_ms": 800,
      "max_tokens": 4096,
      "default": true
    }
  ]
}
```

Current state: single DeepSeek model. Router passes through. Architecture is ready for multi-model.

---

## 6. Permission & Safety Layer (`app/os/guard.py`)

### Interface

```python
class Guard:
    def check(self, action: str, tool: str, user_id: str) -> GuardResult
    def validate(self, response: str) -> SafetyVerdict

class GuardResult:
    allowed: bool
    reason: str
    require_approval: bool

class SafetyVerdict:
    safe: bool
    flags: list[str]  # "hallucination", "harmful", "broken_json"
```

### Permission Rules (`config/permissions.json`)

```json
{
  "rules": [
    {"action": "tool.execute", "tool": "auto_apply", "require_approval": true},
    {"action": "tool.execute", "tool": "files.delete", "require_approval": true},
    {"action": "tool.execute", "tool": "reminder.send", "require_approval": false},
    {"action": "tool.execute", "tool": "*", "require_approval": false},
    {"action": "response.send", "require_approval": false}
  ]
}
```

Rules are checked top-to-bottom. First match wins. `*` is wildcard. `require_approval: true` triggers inline keyboard in Telegram (`[Setuju] [Tolak]`).

### Flow

```
tool.before_execute event
  → Guard.check(action, tool, user_id)
    → allowed? continue
    → require_approval? send Telegram inline keyboard, wait for response
    → denied? block execution, emit error.occurred

response.ready event
  → Guard.validate(response)
    → safe? send response
    → flagged? log warning, optionally block
```

---

## 7. Wiring (`app/app.py` update)

```python
def create_core():
    event_bus = EventBus()
    plugin_loader = PluginLoader(event_bus)
    guard = Guard(event_bus)
    router = LLMRouter()

    # Phase 7A: auto-discover plugins (decorator-based)
    plugin_loader.discover("app/plugins/")

    # Existing: manual wiring
    llm = DeepSeekLLM()
    memory = Memory()
    long_term = LongTermMemory()
    scheduler = Scheduler()
    agent = Agent(llm, memory, scheduler, long_term)

    return agent, memory, scheduler, event_bus
```

---

## 8. Non-Goals (deferred to later phases)

- Dynamic model switching with latency measurement (future: A/B test routing)
- Persistent event log (future: PostgreSQL/ClickHouse)
- Cross-process event bus (future: Redis/RabbitMQ)
- Plugin hot-reload
- Plugin marketplace / external plugin loading
- RBAC with multiple users/roles

---

## 9. Risks & Trade-offs

| Risk | Mitigation |
|---|---|
| Event bus becomes bottleneck under load | In-process is fine for single-user. Upgrade to Redis when multi-user needed. |
| Plugin auto-discovery is implicit (magic) | Decorators are explicit in code. Plugin manifest is explicit in config. |
| Permission rules too coarse-grained | Rule matrix is extensible. Can add conditions (time, context, user) later. |
| Guard.validate() is heuristic (not perfect) | Flags are advisory. Blocking is configurable per rule. |

---

## 10. Test Strategy

Each module is independently testable:
- `EventBus`: emit event, assert handler called with correct payload
- `PluginLoader`: create file with decorator, run discover, assert registered
- `LLMRouter`: provide TaskProfile, assert correct ModelConfig returned
- `Guard.check()`: provide action+tool, assert GuardResult
- `Guard.validate()`: provide response string, assert SafetyVerdict flags

No integration tests required for individual modules. Integration tests cover the full `message.received → plan → execute → respond` pipeline with event bus and guard wired.

---

## 11. Implementation Order

1. `EventBus` — foundation, everything else depends on it
2. `PluginLoader` — decorators + auto-discovery
3. `Guard` — permission + safety (hooks into existing tool execution via events)
4. `LLMRouter` — config-based routing (pass-through for now)
5. Configuration files (`models.json`, `permissions.json`, `plugins.json`)
6. Wire into `app.py` — integrate with existing Agent and Telegram bot
7. Migrate 1-2 existing tools to plugin format as proof-of-concept
