---
name: lab-refactoring-kit-v2
description: When you need to safely improve code quality — combines doubt-driven validation, simplification, test coverage, and quality review in a proven sequence
---

# Refactoring Kit v2

## When to use

Use this composite skill when you need to **safely improve existing code** without breaking behavior. The kit is designed for situations where code works but needs improvement — whether it's unclear, overly complex, under-tested, or inconsistent with project conventions.

Trigger this skill when:
- You've completed a feature under time pressure and want to clean it up before merging
- Code review flagged quality issues (complexity, readability, test coverage)
- You're consolidating duplicate logic across multiple files
- You inherited code that works but is difficult to understand or modify
- You need to prepare code for a major change by simplifying its current state first
- You want to refactor with confidence that you're not introducing regressions

**Do NOT use this skill when:**
- Code is already clean and well-tested — refactoring for its own sake wastes effort
- You're about to rewrite the module entirely — don't refactor throwaway code
- The change is purely mechanical (renaming, formatting) with no complexity or quality concerns
- You're under time pressure to ship — this is a quality investment, not a speed tool

## Pipeline

This is a **sequential flow**. Each step builds on the previous one. Do not skip steps.

### Step 1: Doubt-Driven Validation (eng-doubt-driven-development)

**Goal:** Verify you understand what the code actually does before changing it.

Before refactoring anything, materialize a fresh-context reviewer to validate your mental model of the code's behavior:

1. Write your CLAIM: "This code does X under conditions Y"
2. Extract the artifact (the code to refactor) and the contract (what it must preserve)
3. Invoke a fresh-context reviewer with an adversarial prompt biased to disprove your claim
4. Reconcile findings — if the reviewer catches behavioral misunderstandings, update your model
5. Stop when findings are trivial or you've cycled 3 times

**Why first:** Refactoring code you don't fully understand introduces subtle bugs. Doubt-driven development surfaces hidden assumptions, edge cases, and behavioral nuances before you touch the code. A refactoring that breaks behavior because you missed a side effect is a net-negative outcome.

**Output:** A validated mental model of current behavior + edge cases to preserve.

### Step 2: Test Coverage (eng-test-driven-development)

**Goal:** Lock in current behavior with tests so refactoring can't silently break anything.

Before simplifying or restructuring code, ensure it has test coverage:

1. If tests already exist, run them to confirm they pass and cover edge cases identified in Step 1
2. If tests are missing or incomplete, write them now — treat this as the "RED" phase where you're documenting expected behavior
3. For each test, verify it would **fail** if the implementation were removed (tests that pass with no implementation prove nothing)
4. Focus on behavioral tests (inputs → outputs, including error cases) rather than implementation details

**Why second:** Tests are the safety net. Without them, simplification is guesswork — "did I break something?" becomes "let's deploy and find out." With tests, every refactor step is validated immediately. This step also forces you to externalize the behavioral contract surfaced in Step 1.

**Output:** A passing test suite that will catch regressions during refactoring.

### Step 3: Simplification (eng-code-simplification)

**Goal:** Reduce complexity while preserving exact behavior, validated by tests.

With behavior locked in by tests, simplify the code:

1. **Preserve behavior exactly** — all inputs, outputs, side effects, error behavior, and edge cases remain identical
2. **Follow project conventions** — study neighboring code and match the project's style (see CLAUDE.md)
3. **Prefer clarity over cleverness** — explicit code beats compact code when compact requires mental gymnastics
4. **Maintain balance** — don't over-inline, don't merge unrelated logic, don't remove abstractions that exist for good reasons
5. **Scope to what changed** — default to simplifying recently modified code, avoid drive-by refactors

After each simplification step, **run the test suite** to confirm behavior is preserved.

**Why third:** You can't simplify safely without understanding (Step 1) and validation (Step 2). Simplification is not "making code shorter" — it's making code easier to read, understand, and modify while keeping tests green. The tests tell you immediately if you've broken the behavioral contract.

**Output:** Cleaner code with identical behavior, validated by still-passing tests.

### Step 4: Quality Review (eng-code-review-and-quality)

**Goal:** Verify the refactored code meets project quality standards across all dimensions.

Run a multi-dimensional review of the refactored code:

1. **Correctness** — Does it match the spec? Are edge cases handled? Do tests actually test the right things?
2. **Readability & Simplicity** — Are names descriptive? Is control flow straightforward? Could this be done in fewer lines? Are abstractions earning their complexity?
3. **Architecture** — Does it follow existing patterns? Are module boundaries clean? Is there duplication that should be shared?
4. **Security** — Is user input validated? Are secrets kept out of code? Is external data treated as untrusted?
5. **Performance** — Any N+1 patterns? Unbounded loops? Unnecessary re-renders?

Apply the **approval standard:** Approve when the change definitely improves overall code health, even if it isn't perfect. Perfect code doesn't exist — the goal is continuous improvement.

**Why fourth:** The refactored code is now simpler and validated by tests, but it still needs a quality gate. This step catches issues the other steps don't address — architectural misalignment, security gaps, performance regressions, or readability problems that survived simplification.

**Output:** A quality assessment across five dimensions. If issues are found, return to Step 3 (simplify differently) or Step 2 (add missing tests).

## Why this combination

Each skill addresses a distinct risk in refactoring:

- **Doubt-Driven Development** prevents refactoring based on wrong assumptions (the "I thought it did X but it actually does Y" failure mode)
- **Test-Driven Development** prevents silent regressions (the "it works on my machine but breaks in production" failure mode)
- **Code Simplification** improves maintainability without changing behavior (the "I cleaned it up and introduced three new bugs" failure mode)
- **Code Review** ensures quality across dimensions that testing and simplification don't cover (the "it's simple but insecure / slow / architecturally inconsistent" failure mode)

The **sequential order** is deliberate:
1. You can't simplify what you don't understand → doubt first
2. You can't refactor safely without tests → tests second
3. You can't validate quality until simplification is done → simplification third
4. Review catches what the other steps miss → review last

## Anti-patterns

### When NOT to use this skill

**Don't apply this skill when:**

1. **Code is already clean** — Refactoring for its own sake creates churn. If code is readable, tested, and maintainable, leave it alone.
2. **You're rewriting the module** — Don't refactor code you're about to delete. Invest the effort in the new implementation instead.
3. **Time pressure to ship** — This is a quality investment with a time cost. If the deadline is tight and the code works, ship it and schedule refactoring later.
4. **Purely mechanical changes** — Renaming variables or reformatting code doesn't need this process. Use linters and formatters instead.
5. **You don't own the code** — Don't refactor third-party dependencies or code owned by another team without coordination.

### Skill conflicts and how to resolve them

This composite combines skills with potentially conflicting advice. Here's how to synthesize them:

**Conflict: Doubt-driven says "spawn fresh-context reviewer" but you're in a nested subagent context where that's forbidden**

- **Resolution:** Surface to the user that doubt-driven review cannot run nested. Offer the degraded self-questioning fallback (rewrite ARTIFACT + CONTRACT as a fresh self-prompt with mental separator), but flag it as not-truly-fresh-context. Prefer escalation to the main session whenever possible.

**Conflict: Simplification says "prefer clarity" but Review says "fewer lines is better"**

- **Resolution:** Simplification takes precedence. Review's "fewer lines" guidance means "don't write 1000 lines where 100 suffice" (avoiding bloat), not "compress everything into the fewest possible lines" (sacrificing clarity). When in doubt, optimize for the "would a new team member understand this faster?" test from Simplification.

**Conflict: TDD says "write minimal code to pass tests" but Simplification says "remove duplication"**

- **Resolution:** Apply them in sequence. During Step 2 (TDD), write minimal code — duplication is fine in the GREEN phase. During Step 3 (Simplification), remove the duplication while keeping tests green. The refactor step of TDD explicitly allows this.

**Conflict: Doubt-driven flags architectural concerns but Simplification says "scope to what changed"**

- **Resolution:** Simplification's scoping rule prevents drive-by refactors of unrelated code. If Doubt-driven surfaces architectural issues in the code you're actively refactoring, that's in-scope. If it surfaces issues in distant modules, defer them (file an issue, notify the user) rather than expanding the refactoring scope.

**Conflict: Review flags performance issues but Simplification says "don't optimize prematurely"**

- **Resolution:** Review's performance check catches obvious anti-patterns (N+1 queries, unbounded loops). Simplification's anti-optimization guidance prevents micro-optimizations that sacrifice clarity. If Review flags a clear performance problem, fix it. If it's a hypothetical optimization ("this could be 10% faster with a different data structure"), skip it unless profiling data justifies it.

### Common failure modes

1. **Skipping tests** — "The code is simple enough, tests would be overkill." This is how regressions happen. Always run Step 2.
2. **Over-simplifying** — Removing abstractions that exist for extensibility or testability. Simplification is not minimalism — maintain balance.
3. **Ignoring project conventions** — Simplifying code in a style inconsistent with the rest of the codebase creates cognitive load, not clarity.
4. **Refactoring under uncertainty** — If you don't understand what the code does (Step 1 doubt-cycle didn't converge), stop and ask for human guidance.
5. **Expanding scope mid-refactor** — You started simplifying one function and ended up rewriting three modules. Return to the original scope or explicitly ask the user to expand it.

---

**Remember:** Refactoring is a quality investment, not a speed tool. The pipeline is designed to make refactoring safe and systematic. If you're tempted to skip steps, ask yourself: "Am I willing to bet that this change introduces no regressions?" If not, follow the process.