---
name: lab-refactoring-kit
description: When you need to safely improve code quality through a systematic pipeline of doubt → simplification → testing → review
---

# Refactoring Kit

## When to use

Use this composite skill when you need to refactor existing code safely and systematically. This is for situations where code works but needs quality improvement — whether it's unclear, overly complex, untested, or poorly structured. The pipeline ensures you improve code health without breaking behavior.

**Trigger conditions:**
- Code review identified readability or complexity issues
- Working feature needs cleanup before merge
- Legacy code needs modernization
- Technical debt paydown sprint
- Post-incident refactoring (after the fire is out)
- Consolidating duplicated logic across modules

**When NOT to use:**
- Code is already clean and well-tested
- You're building a new feature from scratch (use `eng-test-driven-development` alone)
- You're fixing a critical production bug (fix first, refactor later)
- You don't understand what the code does yet (comprehend before refactoring)
- The module is scheduled for complete rewrite

## Pipeline

### Step 1: Run `eng-doubt-driven-development` first

**Purpose:** Surface hidden assumptions and verify your understanding before touching anything.

Before refactoring, you must understand what the code actually does — not what you think it does. Apply doubt-driven development to:

1. Write down your claim: "This function does X under constraints Y"
2. Extract the artifact (the code to be refactored) and its contract (inputs, outputs, side effects, edge cases)
3. Spawn a fresh-context reviewer to challenge your understanding
4. Reconcile findings until you have high confidence

**Success criteria:** You can articulate what the code does, why it does it that way, and what constraints it must satisfy. All non-trivial assertions about behavior have been cross-examined.

**On failure:** If doubt-driven development reveals you misunderstood the code, STOP. Do not proceed to simplification until you comprehend the actual behavior. Misunderstood code + refactoring = production incident.

### Step 2: Run `eng-test-driven-development` to establish safety net

**Purpose:** Lock in current behavior with tests before changing anything.

If tests don't already exist (or coverage is incomplete), write them now using TDD's Prove-It Pattern:

1. Write tests that pass against the current implementation (these are characterization tests)
2. Cover all edge cases, error paths, and integration points identified in Step 1
3. Confirm 100% of the code you're about to refactor is exercised by tests
4. Document any behavior that seems wrong but is intentional (to preserve or fix knowingly)

**Success criteria:** Full test coverage of the refactoring target. All tests green. You can now refactor with confidence — if tests stay green, behavior is preserved.

**On failure:** If you cannot write tests (code is untestable), that's a finding. Consider Step 3 (simplification) might need to include "extract testable units" as a refactoring goal.

### Step 3: Run `eng-code-simplification`

**Purpose:** Improve code clarity while preserving exact behavior.

Now simplify, guided by these principles (from `eng-code-simplification`):

1. **Preserve behavior exactly** — tests from Step 2 must stay green
2. **Follow project conventions** — match the codebase style
3. **Prefer clarity over cleverness** — explicit beats compact
4. **Maintain balance** — don't over-simplify (removing useful abstractions)
5. **Scope to what changed** — avoid drive-by refactors

**Incremental approach:**
- Make one simplification at a time
- Run tests after each change
- Commit when tests are green (or at least checkpoint your work)
- If tests break, revert and understand why before re-attempting

**Common simplifications:**
- Extract functions to name concepts
- Flatten nested conditionals
- Replace clever one-liners with readable multi-step logic
- Consolidate duplicated code
- Improve variable/function names
- Remove dead code and unused abstractions

**Success criteria:** Code is easier to understand. Tests still pass. No new complexity introduced. A new team member would grasp this faster than the original.

**On failure:** If simplification breaks tests, revert. Re-examine Step 1 (did you misunderstand something?) or Step 2 (are tests incomplete?). Do not proceed with broken tests.

### Step 4: Run `eng-code-review-and-quality`

**Purpose:** Multi-dimensional quality check before considering the refactoring done.

Review the refactored code across all five axes:

1. **Correctness:** Does it still do what it claims? Tests pass, but are tests correct?
2. **Readability & Simplicity:** Is it clearer than before? Could it be simpler still?
3. **Architecture:** Does it fit the system better now? Any new abstractions justified?
4. **Security:** Did refactoring accidentally expose vulnerabilities? (Review external data flows, input validation)
5. **Performance:** Did simplification introduce performance regressions? (Check for N+1 patterns, unnecessary allocations)

**The approval standard (from `eng-code-review-and-quality`):** Approve when the change definitely improves overall code health, even if it isn't perfect. The refactoring should be better than the original — not perfect in absolute terms.

**Success criteria:** All five axes score better than or equal to the original. No new issues introduced. Change improves code health.

**On conflict:** If review identifies issues, loop back:
- Correctness/security issues → return to Step 1 (doubt-driven) or Step 2 (add tests)
- Readability issues → return to Step 3 (simplify more, or undo over-simplification)
- Architecture issues → might be out of scope for this refactoring (file as future work)

### Step 5: Final verification

Before declaring done:

- [ ] All tests green (Step 2)
- [ ] Code is simpler/clearer than original (Step 3)
- [ ] Five-axis review passed (Step 4)
- [ ] No regressions in test suite (run full suite, not just local tests)
- [ ] Commit message explains *why* this refactoring (not just *what* changed)

If any checkbox fails, return to the appropriate step.

## Why this combination

Each skill addresses a failure mode:

- **Doubt-driven development (Step 1)** prevents "confident but wrong" refactoring. You can't safely simplify code you don't understand.
- **Test-driven development (Step 2)** prevents "looks better but broke something" refactoring. Tests lock in behavior before you touch it.
- **Code simplification (Step 3)** is the actual refactoring work, constrained by Steps 1–2 to preserve correctness.
- **Code review (Step 4)** catches issues the other steps don't: security implications, performance regressions, architectural misalignment.

**The skills synthesize, they don't conflict:**

- Doubt-driven and TDD both value verification, but at different moments: doubt-driven verifies *understanding* before action, TDD verifies *behavior* during action.
- Simplification and review have tension (simplify vs. perfect), which is healthy: simplification prevents analysis paralysis, review prevents reckless simplification.
- TDD and review overlap on correctness, but TDD is mechanical (tests pass/fail) while review is judgmental (does this test the right thing?).

**When skills disagree:**

- If doubt-driven reveals complexity you don't understand, but simplification wants to flatten it: **doubt-driven wins**. Understand first, simplify second.
- If simplification wants to inline a function, but review says the abstraction has architectural value: **review wins**. Simplification optimizes for local readability; review considers system-wide coherence.
- If TDD says tests pass, but doubt-driven questions whether behavior is correct: **doubt-driven wins**. Passing tests on wrong behavior is false confidence.

The pipeline order encodes these priorities: understand → guard → improve → verify.

## Anti-patterns (when NOT to use)

### Anti-pattern 1: Refactoring during a feature build

**Symptom:** "While implementing feature X, I noticed this code could be cleaner, so I'll refactor it first."

**Why it fails:** You're mixing two change types (feature + refactor) in one PR, making review harder and rollback riskier. You also don't yet know if the code *needs* refactoring for your feature — you might be refactoring the wrong thing.

**Instead:** Finish the feature first. If code needs refactoring to enable the feature, do it in a separate commit/PR with tests proving behavior is preserved, *then* build the feature on the refactored base.

### Anti-pattern 2: Refactoring without tests

**Symptom:** "I'll simplify this code and add tests after."

**Why it fails:** You have no proof you preserved behavior. "Looks right" is not done. Tests-after means you're testing your refactored code, not the original behavior — you'll miss bugs you introduced.

**Instead:** Always Step 2 (TDD) before Step 3 (simplification). Write tests against the current (pre-refactor) implementation, then refactor with tests as guardrails.

### Anti-pattern 3: Refactoring code you don't understand

**Symptom:** "This code is messy and I don't fully understand it, but I'll clean it up anyway."

**Why it fails:** You'll break subtle invariants or edge case handling that wasn't obvious. The mess might be defending against real constraints.

**Instead:** Step 1 (doubt-driven) is mandatory. If you can't articulate what the code does and why, you're not ready to refactor it. Comprehension first, refactoring second.

### Anti-pattern 4: Over-simplifying for line count

**Symptom:** "I reduced this from 100 lines to 30 by chaining array methods."

**Why it fails:** Fewer lines ≠ simpler. Deeply nested array chains or dense one-liners are harder to debug and understand than explicit loops with named intermediate variables.

**Instead:** Step 3 (simplification) principle #3: prefer clarity over cleverness. Step 4 (review) axis #2 will catch this — readability should improve, not degrade.

### Anti-pattern 5: Refactoring unrelated code (scope creep)

**Symptom:** "While refactoring module A, I noticed modules B, C, D also need cleanup, so I'll do them all now."

**Why it fails:** Unbounded scope makes the change risky, hard to review, and hard to roll back if something breaks. You're also mixing concerns.

**Instead:** Scope the refactoring to the change that triggered it. If you discover other areas needing work, file them as separate tasks. One refactoring per PR.

### Anti-pattern 6: Skipping Step 4 (review) because "tests pass"

**Symptom:** "Tests are green, so the refactoring is done."

**Why it fails:** Tests prove correctness, not readability, architecture fit, security, or performance. A correct but unreadable refactoring failed its purpose.

**Instead:** Run Step 4 (code review) even if you're the only reviewer. The five-axis review catches issues tests can't.

### Anti-pattern 7: Refactoring critical path code without a rollback plan

**Symptom:** "This is production code with no issues, but it's not clean, so I'll refactor it."

**Why it fails:** If the refactoring introduces a bug (despite tests), you need a fast rollback path. Refactoring working production code is low-priority unless it's blocking a feature or causing operational pain.

**Instead:** Deprioritize refactoring code that works and isn't changing. If you must refactor it, use feature flags or deploy strategies that allow instant rollback.

---

**Final note on skill conflicts:** This pipeline intentionally includes skills with different philosophies (doubt-driven's skepticism vs. simplification's action bias, TDD's mechanical verification vs. review's human judgment). The conflicts are productive — they prevent overconfidence, recklessness, and analysis paralysis. When skills disagree, the earlier step in the pipeline wins, because the pipeline encodes a safety order: understand → guard → improve → verify.