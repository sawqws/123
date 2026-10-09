---
name: lab-idea-pipeline
description: When you need to take a raw startup idea from zero to validated direction — combines user interview, refinement, and dual-pressure testing (Andreessen market lens + Graham startup lens) in sequence.
---

# Idea Pipeline

## When to use

Use this skill when the user has a raw startup idea (or just a hunch) and wants to go from "maybe this?" to "here's what to build and why" without skipping critical validation steps. This is the full pipeline: understand what they actually want (interview), generate and refine variations (refine), then pressure-test the result from two different angles (Andreessen's market-first lens and Graham's early-startup lens). If the user already has a validated direction and just needs one piece (e.g., "pressure-test this spec"), use the atomic skills directly instead.

## Pipeline

Run these skills in order. Each step feeds the next. Do not skip steps unless the user explicitly asks to start mid-pipeline.

### 1. Run `eng-interview-me` first

**Goal:** Surface what the user actually wants before refining or testing anything.

- The user says "I have an idea" but hasn't articulated who it's for, why now, what success looks like, or what the binding constraint is.
- Use `eng-interview-me` to close that gap: one question at a time, each with a guess attached, until you can predict their answers at ≥70% confidence.
- **Output checkpoint:** A crisp "How Might We" problem statement + clarity on ICP, success criteria, and constraints. Write this down explicitly before moving to step 2.
- **If the user resists interview mode:** Some users want to skip straight to refinement or testing. That's fine — flag that you're working with incomplete input and proceed, but mark assumptions as unvalidated in the final output.

### 2. Run `eng-idea-refine` next

**Goal:** Take the validated problem statement from step 1 and generate sharp, differentiated variations.

- Use the divergent phase to create 5-8 variations (inversion, constraint removal, audience shift, simplification, 10x version, expert lens).
- If running inside a codebase, ground variations in actual architecture and patterns (use `Glob`, `Grep`, `Read`).
- Move to convergent mode: cluster the variations the user resonated with into 2-3 distinct directions, stress-test each against user value / feasibility / differentiation.
- **Output checkpoint:** A markdown one-pager per direction (or a combined doc with 2-3 directions), each containing:
  - Problem Statement
  - Recommended Direction
  - Key Assumptions
  - MVP Scope
  - Not Doing list
- Save this to `docs/ideas/[idea-name].md` (get user confirmation on the filename).

### 3. Run `ar-productivity-andreessen` pressure test

**Goal:** Test the refined idea(s) against Andreessen's market-first lens.

- For each direction from step 2, run the **market-first evaluation** (`market_first_evaluator.py`):
  - Is there a market? (size, growth, urgency)
  - Is the market a hard gate? (weak market = no team or product brilliance rescues it)
  - Does the team have an edge in this market?
- Run the **forcing-question interrogation** (from `ar-productivity-andreessen`'s workflow): walk the standard forcing questions one at a time, leading each with a recommended answer, before issuing a verdict.
- Use the **Andreessen operating prompt** voice: lead with the strongest counterargument, no validation of premises, explicit confidence levels on every claim, generate your own numbers first (don't anchor on user estimates), never hallucinate (say "unknown" if you can't verify), don't capitulate under pushback unless given new evidence.
- **Output checkpoint:** For each direction, a verdict: `STRONG GO` / `CONDITIONAL GO` / `STOP`. Include:
  - Market reality (size, growth, urgency, confidence level)
  - Fatal flaws (if any)
  - What must be true for this to work
  - Recommended next action (usually: validate the riskiest assumption manually)

### 4. Run `kp-startup-pressure-test` in parallel

**Goal:** Test the same idea(s) against Graham's early-startup lens (real users, painful problems, current behavior, manual traction).

- For each direction from step 2, run `kp-startup-pressure-test` in **full mode** (compact output by default):
  - Verdict (Strong / Weak / Pivot required)
  - Scorecard (pain intensity, buyer clarity, urgency, differentiation, speed to validate, founder advantage)
  - Core assumption (one sentence: what must be true for this to work)
  - Fatal flaws (ranked by severity, with fast tests)
  - Problem reality (pain type, early adopter profile, vitamin vs painkiller)
  - Competition (current behavior, real enemy, differentiation needed)
  - First 10 customers (manual founder-led plan)
  - MVP (build/cut/2-week test)
- Use the compact output shape from `kp-startup-pressure-test` (scorecard always 6 rows, verdict max 3 sentences, fatal flaws max 3 rows, etc.). Do not expand unless the user asks for `deep` mode.
- **Output checkpoint:** For each direction, a Graham-lens report (markdown table format). Save to `docs/ideas/[idea-name]-graham-test.md`.

### 5. Synthesize the dual pressure test

**Goal:** Reconcile the Andreessen and Graham verdicts into a single recommendation.

- **If both say GO:** The idea passed both lenses. Recommend moving to `eng-spec-driven-development` or `eng-doubt-driven-development` to write the spec and stress-test the plan.
- **If Andreessen says STOP but Graham says STRONG:** The market lens sees a fatal flaw (usually: market too small, no urgency, or team mismatch), but the early-startup lens sees real pain and a clear path to first customers. Interpretation: This might work as a niche play or a wedge, but it won't scale to VC-backable size without a pivot. Recommend: validate the Graham path manually (first 10 customers), then revisit the market lens after you have traction data.
- **If Graham says WEAK but Andreessen says GO:** The early-startup lens sees low pain intensity, unclear buyer, or weak differentiation, but the market lens sees a huge market and urgency. Interpretation: The idea might work at scale with the right execution, but you haven't found the early adopter wedge yet. Recommend: go back to `eng-interview-me` and `eng-idea-refine` to sharpen the ICP and MVP scope. Don't build the big vision first — find the painful niche.
- **If both say STOP:** The idea has fatal flaws on both lenses. Recommend: pivot or kill. If the user wants to keep exploring, go back to `eng-idea-refine` and generate new variations (different audience, different constraint, different wedge). Do not proceed to spec or build without a new direction.
- **If verdicts are CONDITIONAL on both sides:** List the conditions side by side. If they overlap (e.g., both say "validate demand manually first"), that's the clear next action. If they conflict (e.g., Andreessen says "prove the market is 10x bigger" and Graham says "cut scope to test one painful use case"), explain the tradeoff: market-first thinking optimizes for scale, early-startup thinking optimizes for fast learning. Recommend: start with the Graham path (manual validation of the painful use case) because it's faster and cheaper, then use that traction data to re-test the Andreessen conditions.

**Output checkpoint:** A synthesis doc saved to `docs/ideas/[idea-name]-verdict.md`, structured as:

```markdown
# [Idea Name] — Final Verdict

## Recommendation
[GO / CONDITIONAL GO / PIVOT / STOP]

[2-3 sentences: what to do next]

## Andreessen Lens (Market-First)
- Verdict: [STRONG GO / CONDITIONAL GO / STOP]
- Market reality: [size, growth, urgency, confidence level]
- Fatal flaws: [if any]
- Conditions: [what must be true]

## Graham Lens (Early Startup)
- Verdict: [Strong / Weak / Pivot required]
- Scorecard: [paste the 6-row table]
- Core assumption: [one sentence]
- Fatal flaws: [ranked by severity]
- First 10 customers: [manual plan]
- MVP: [build/cut/2-week test]

## Synthesis
[How the two lenses agree or disagree, and why. If they conflict, explain the tradeoff and recommend a path.]

## Next Action
[Concrete next step: validate X manually, go back to refinement, write a spec, or kill the idea.]
```

## Why this combination

- **Interview → Refine → Dual Pressure Test** mirrors how real founders de-risk ideas: first, get clear on the problem (interview); second, generate alternatives and pick the best one (refine); third, stress-test it from multiple angles before committing resources (pressure test).
- **Andreessen + Graham** cover different failure modes: Andreessen catches market-level traps (too small, no urgency, wrong team), Graham catches execution-level traps (fake pain, unclear buyer, no manual path to first customers). An idea can pass one lens and fail the other — that's useful signal, not a bug.
- **Sequential, not parallel (except step 3 + 4):** You can't refine an idea you haven't understood (interview must come first), and you can't pressure-test variations you haven't generated (refine must come before pressure test). But once you have the refined directions, the two pressure tests are independent — run them in parallel to save time.
- **Synthesis step is mandatory:** The user gets two verdicts that might conflict. Your job is to reconcile them into a single recommendation, not leave the user with "well, it depends." If the lenses disagree, explain the tradeoff and pick a path.

## Anti-patterns (when NOT to use)

- **The ask is unambiguous and self-contained:** If the user says "rename this variable" or "fix this typo," do not run the pipeline. Apply the atomic skill directly (or just do the work).
- **The user has already validated the idea elsewhere:** If they say "we have 50 paying customers and we're refactoring the architecture," they don't need interview or pressure-testing — they need `eng-spec-driven-development` or `eng-doubt-driven-development` directly.
- **The user explicitly asks for speed over validation:** Some users want to prototype fast and learn by building. That's fine — skip the pipeline and go straight to `eng-spec-driven-development` or start coding. Flag that you're skipping validation, but don't block them.
- **Non-startup contexts:** This pipeline is startup-focused (market size, early adopters, PMF, etc.). If the user is building an internal tool, a research prototype, or a personal project, the pressure-test steps (especially Andreessen's market-first lens) won't apply cleanly. Use `eng-interview-me` + `eng-idea-refine` + `eng-doubt-driven-development` instead (drop the startup-specific pressure tests).
- **The idea is already killed by one lens:** If Andreessen says "no market, hard stop" and the user agrees, don't run Graham's test just to complete the pipeline. Save the time and move to the next variation or pivot.
- **You're in a non-interactive context:** If you're in CI, a scheduled run, or `/loop`, you can't run `eng-interview-me` (it requires a live user). Flag the missing inputs as a blocker instead of running the pipeline.

---

**Final note on conflict synthesis (Grisha's principle):** When Andreessen and Graham disagree, do not treat one as "right" and the other as "wrong." Both lenses are correct within their frame. Your job is to explain the tradeoff (scale vs. speed, market size vs. pain intensity, VC-backable vs. bootstrappable) and recommend a path that acknowledges both perspectives. The best ideas pass both lenses. The interesting ideas pass one and teach you something about the other.