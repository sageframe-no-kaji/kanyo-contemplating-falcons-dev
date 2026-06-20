# HO to Agent Task Protocol

## Purpose

This document describes a reusable workflow for turning a **HO** into an **agent-ready execution brief**.

It is derived from the observed Kanyo workflow evolution in late December 2025 and early January 2026.

The core idea is:

**Do not hand the raw HO directly to the coding agent by default.**

Instead:

`HO` -> `translation / compression / restructuring` -> `Agent Task`

This protocol formalizes that translation layer.

---

## Why This Protocol Exists

A HO and an Agent Task do different jobs.

### The HO is for:

- planning
- rationale
- architecture
- learning
- retrospective continuity

### The Agent Task is for:

- execution
- constraints
- exact file changes
- validation
- bounded scope

If you send a raw HO to an implementation agent, several problems appear:

- too much narrative
- too much non-actionable context
- unclear task boundaries
- unclear stop conditions
- higher chance of agent drift

So the protocol exists to preserve the intelligence of the HO while removing the ambiguity that makes execution worse.

---

## High-Level Model

The protocol has three layers:

### Layer 1: HO

The durable human thinking document.

Contains:

- purpose
- problem framing
- architecture
- lessons learned
- what matters strategically

### Layer 2: Translation Layer

Usually performed by a planning model or chat model.

Its job is to:

- identify the actionable slice of the HO
- remove irrelevant narrative
- preserve critical context
- convert intent into execution language

### Layer 3: Agent Task

The execution artifact given to the coding agent.

Contains:

- exact problem
- exact files
- exact constraints
- acceptance criteria
- verification commands

---

## Core Principle

The translation step should answer:

**What does the coding agent need in order to act correctly, and what can be removed without harming correctness?**

The translation should not simply summarize.

It should transform:

- from reflective language
- into executable language

This is the difference between a note and an operational brief.

---

## Input Assumptions

The protocol assumes the HO may contain all of the following mixed together:

- architecture notes
- design rationale
- implementation history
- debugging discoveries
- metrics
- emotional framing or motivation
- future ideas
- partial instructions

The coding agent does not need all of that at once.

The protocol extracts only the task-relevant portion.

---

## The Translation Process

### Step 1: Identify the task nucleus

Read the HO and isolate the smallest actionable unit.

Ask:

- What is the real problem to solve right now?
- Is this a bug fix, refactor, feature, deployment task, or audit?
- What specific outcome defines success?

Output of this step:

- one crisp task statement

Example:

- too broad: `state machine redesign`
- task nucleus: `remove ACTIVITY-related dead code without changing visit behavior`

---

### Step 2: Separate execution from reflection

Strip out material that is useful for understanding history but not needed for direct implementation.

Usually remove:

- full origin story
- broad future possibilities
- long retrospective prose
- unrelated lessons learned
- metrics not needed for correctness

Usually keep:

- current bug or change target
- key invariants
- important architecture constraints
- terminology needed to avoid misunderstandings

Rule:

If a detail would not change what the agent edits, do not include it unless it prevents a likely mistake.

---

### Step 3: Name the scope explicitly

Convert implied scope into declared scope.

State:

- files to inspect
- files expected to change
- systems that must not change
- whether tests should be updated

This is where ambiguity drops sharply.

The agent should not have to infer the write boundary if you already know it.

---

### Step 4: State invariants and non-goals

This is one of the most important steps.

Every good Agent Task should say not only what to do, but what must remain true.

Include:

- behavior that must not regress
- architecture that must stay intact
- features that must not be touched
- timing / state assumptions that must remain unchanged

Good examples:

- `Do not change the time-based state machine logic.`
- `Do not modify visit clip behavior.`
- `Do not change thresholds.`
- `Do not redesign buffer mechanics.`

This protects the HO's deeper intent during execution.

---

### Step 5: Convert desired outcome into concrete edits

Translate the task into explicit required changes.

Prefer:

- `In file X, update Y`
- `Remove method Z`
- `Add field A`
- `Replace logic B with C`

Avoid:

- vague goals like `clean this up`
- aesthetic prompts like `make it better`
- broad asks like `refactor for clarity`

The more mature the understanding, the more explicit the edit instructions should become.

---

### Step 6: Define acceptance criteria

An Agent Task should have a visible finish line.

Include:

- what must be true after the change
- how to inspect it
- what evidence counts as success

Examples:

- `Arrival clip offsets are computed from the anchor frame only.`
- `No ACTIVITY references remain in buffer_monitor.py.`
- `Departure clip shows the actual departure, not empty nest.`

This makes the task checkable rather than interpretive.

---

### Step 7: Add verification commands

If the project already has validation patterns, include them.

Examples:

```bash
black src/ tests/
flake8 src/ tests/
mypy src/
pytest tests/ -v
```

This matters because the validation step is part of the execution contract, not an optional afterthought.

---

### Step 8: Add stop conditions

When useful, define the stopping boundary explicitly.

Examples:

- `STOP HERE. Do not proceed to phase 3.`
- `Do not fix unrelated playback issues in this task.`
- `Leave production deployment out of scope.`

This is especially useful when one HO contains multiple phases but you want the agent to execute only one.

---

## Output Format

The observed late-Kanyo Agent Task format converged on a useful structure:

```markdown
# Agent Task: <title>

## Context
Short explanation of what has already happened and what assumptions hold.

## Problem
Exact issue to solve.

## Goal
Desired end state.

## Files to Change
- file A
- file B

## Do Not
- invariant 1
- invariant 2

## Required Changes
1. Change X in file A
2. Remove Y in file B
3. Update tests if needed

## Acceptance Criteria
- condition 1
- condition 2

## Verification
```bash
<commands>
```

## Commit
```bash
git commit -m "<message>"
```
```

This exact shape can vary, but the semantic components should remain.

---

## Compression Rules

When converting a HO into an Agent Task, compress aggressively, but never compress away the constraints that preserve correctness.

### Safe to compress

- project backstory
- general motivation
- repeated explanations
- descriptive narrative
- post-hoc interpretation

### Dangerous to compress away

- state invariants
- file targets
- exact timing assumptions
- boundary conditions
- hidden architecture dependencies
- validation expectations

Useful rule:

**Compress prose, not correctness.**

---

## Decision Rule: Short Prompt vs Full Agent Task

Not every HO slice should become a long Agent Task.

### Use a short prompt when:

- the problem is still fuzzy
- you are exploring root cause
- the scope is small and local
- you want diagnosis more than execution

### Use a full Agent Task when:

- the architecture is understood
- you know what should change
- the task touches multiple files
- regression risk is meaningful
- correctness depends on constraints

This matches the Kanyo evolution:

- early work benefited from short operational prompts
- later work benefited from explicit execution briefs

---

## Likely Historical Kanyo Pattern

The best reconstruction of the real workflow is:

1. A HO was written or updated.
2. Claude chat was used to interpret the HO and isolate the current executable slice.
3. Claude chat rewrote that slice into a coding-agent prompt.
4. The coding agent executed against that transformed prompt.
5. The result was later documented again in a HO or Agent Task archive.

So the HO and the Agent Task formed a loop:

`HO -> execution brief -> implementation -> HO/Task documentation`

This is not duplication. It is a two-layer memory system:

- human continuity layer
- machine execution layer

---

## Protocol Checklist

Before sending a HO-derived task to a coding agent, verify:

- Is the actual problem statement explicit?
- Are the files to inspect or change named?
- Are the non-goals stated?
- Are the invariants preserved?
- Is the task bounded?
- Is there an acceptance test?
- Is there a validation command set?
- Is unnecessary narrative removed?

If any answer is no, the HO has not yet been fully translated into an Agent Task.

---

## Minimal Translation Template

Use this when generating an Agent Task from a HO:

```markdown
# Agent Task: <Task Name>

## Context
- What already changed
- What assumptions now hold

## Problem
- What is wrong or incomplete

## Goal
- What must be true after this task

## Files
- Files to inspect
- Files expected to change

## Do Not
- Behaviors that must remain unchanged
- Systems not in scope

## Required Changes
1. Exact change
2. Exact change
3. Exact change

## Acceptance Criteria
- Observable success condition
- Observable success condition

## Verification
```bash
<project validation commands>
```

## Stop Condition
- What not to continue into
```

---

## Final Rule

The HO should remain the source of deeper intent.

The Agent Task should become the source of executable precision.

Do not collapse these into one object unless the task is trivial.

For non-trivial work, the most reliable system is:

- **HO for thinking**
- **translation layer for distillation**
- **Agent Task for execution**

That is the clearest reusable lesson from the Kanyo workflow.
