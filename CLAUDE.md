# AI Werewolf Contract

@docs/system/Infrastructure system.md
@docs/system/Model Adapter.md
@docs/system/Action System.md
@docs/system/Agent Runtime.md
@docs/system/Game Engine.md
@docs/system/Evaluation System.md
@docs/system/Event System.md
@docs/system/Frontend Interaction System.md
@docs/system/Memory System.md
@docs/system/Observability System.md
@docs/system/Phase System.md
@docs/system/Prompt System.md
@docs/system/Replay System.md
@docs/plan/*.md/

## Architecture

- FastAPI is ingress only
- Heavy compute runs in Celery workers
- LangGraph never executes in API handlers

---

## Authority

Game engine is final authority.

LLM may:

- reason
- strategize
- generate dialogue

LLM may never:

- mutate canonical state
- decide legality
- resolve rules
- decide winners
- advance phases

---

## Memory Isolation

Visibility is enforced in code, never prompts.

Scopes:

- PUBLIC
- PRIVATE
- FACTION

Agents may only access authorized memory.

---

## Constants

No magic strings.

Use enums from:

`schemas/enums.py`

Never:

```python
if phase == "DAY"
```

Always:

```python
if phase == GamePhase.DAY
```

---

## IDs

Use:

`utils/snowflake.py`

Never ad-hoc UUID/random IDs.

---

## Config

All env/config loads go through:

`config.py`

Never use:

```python
os.getenv()
```

outside config layer.

---

## Prompts

Prompt templates are isolated.

Never inline prompts into runtime logic.

---

## Runtime Rules

Always async.

Always typed.

Always structured logging.

Never:

- print()
- silent except
- blocking IO
- untyped dict contracts

---

## LLM Output Safety

Always:

parse → repair → retry → fallback

Never trust raw JSON.

---

## Modification Rules

Prefer minimal changes.

Always reuse existing abstractions before introducing new ones.

Never:

- rewrite unrelated files
- create duplicate utilities
- introduce placeholder/mock implementations
- silently swallow exceptions
- bypass validation/tests

Failures must be explicit and observable.

---

## Comments

All code comments and docstrings must use Simplified Chinese.

Comments must explain:

- 为什么这样设计
- 边界条件
- 异常处理原因

Do not write redundant comments that only describe obvious syntax.

---

## Workflow

Always:

1. inspect existing code
2. produce plan
3. validate architecture
4. implement incrementally
5. run verification
6. summarize changes

If uncertain:

stop and plan

Never guess.