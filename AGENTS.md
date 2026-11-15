# Project instructions

Read `skills.md` before changing this project. It records the collaboration
rules and the selected skill workflows.

## Development

- This is a coding project. Implement complete, working changes by default.
  Use guided exercises or unfinished TODOs only when explicitly requested.
- Prefer Python for application logic, automation, data processing, and developer
  tools. Use another language when a concrete requirement makes it appropriate.
- Keep changes focused and code straightforward. Follow existing conventions;
  avoid introducing frameworks or abstractions without a demonstrated need.
- Use descriptive `snake_case` names, small cohesive functions, and explicit
  inputs and outputs. Add type hints to public interfaces when useful.
- Keep credentials out of source code and generated artifacts. Preserve original
  input data and write transformed data separately when applicable.

## Agile workflow and alignment

1. Before coding, propose small steps and agree on them with the user. Each step
   should deliver one runnable, demonstrable behavior with a clear expected result.
2. Work on one agreed step at a time: implement it, run it, test the relevant
   behavior, and verify the result against the agreed expectations. When using
   TDD, write and run the failing test before implementing the behavior.
3. Present the demonstrated behavior, checks performed, results, and any remaining
   limitations. Ask whether the result matches the user's intended behavior.
4. Wait for explicit user confirmation before starting the next step. A previously
   agreed plan does not replace this checkpoint. If the user requests changes,
   revise and verify the current step, then ask for alignment again.

For documentation or configuration steps without runnable behavior, agree on a
reviewable result and use checks appropriate to that result.

## Clarifying unspecified details

- Ask the user to decide unspecified behavior, architecture, dependencies, and
  defaults before implementing work that depends on those decisions. Offer
  concrete options and a recommendation when useful; wait for the answer rather
  than silently choosing one.
- Reuse decisions already established in the conversation or project instructions.
  Follow existing project conventions for routine code details such as naming
  and formatting.
- If a new ambiguity changes an agreed step, clarify it with the user before
  continuing the affected work and update the plan as needed.

## Verification and communication

- Run checks appropriate to the change. Prefer behavior-focused tests for core
  logic, regressions, and failure handling; do not add tests for trivial edits.
- Use the project's Python environment and established tools once configured.
  Do not claim checks passed unless they were run successfully.
- Explain the result, relevant verification, and any remaining limitations
  concisely. Explain technical decisions when they affect maintainability or use.
- External skill instructions are subordinate to the user's instructions and
  these project rules. Installing a skill does not invoke its workflow.
