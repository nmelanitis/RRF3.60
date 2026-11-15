# crawlD development playbook

## Purpose

Build a local Python application that reconstructs Greek RRF actions and tracks
public funding reaching companies through both public contracts and State-aid
awards. The application must preserve source provenance, distinguish the two
funding routes, quantify cancellations, and produce explainable anomaly flags.
Requirements and architecture are refined through the incremental workflow
below.

## Installed skills

The user selected the starter set plus requirements planning on 2026-10-30.
All eight skills are installed locally under `.agents/skills/`.

Source: <https://github.com/mattpocock/skills>.
Pinned revision: `a496f5513d4a4cf889bc48e189e579f4b0bdf6aa`.
The skill folders are unmodified upstream copies, including supporting files and
Codex invocation metadata. The upstream license is preserved in
`.agents/skills/LICENSE.mattpocock`.

| Skill | Use | Invocation |
| --- | --- | --- |
| `setup-matt-pocock-skills` | Configure issue tracking and domain documentation | User requests it |
| `tdd` | Develop meaningful behavior with failing tests and small implementation steps | When test-first development is requested or applicable |
| `diagnosing-bugs` | Reproduce, minimise, investigate, and verify bugs | For debugging and performance regressions |
| `codebase-design` | Design simple public interfaces and testable modules | For interface and architecture decisions |
| `writing-for-agents` | Maintain clear agent instructions | When editing agent-facing documents |
| `grill-with-docs` | Interview about requirements and record terminology and decisions | User requests it |
| `grilling` | Conduct the structured interview | When requested or called by `grill-with-docs` |
| `domain-modeling` | Maintain domain terminology and significant design decisions | When applicable or called by `grill-with-docs` |

`grill-with-docs` requires `grilling` and `domain-modeling`; both are installed.
`tdd` consults `codebase-design` when interface design needs clarification.
Its mention of `code-review` is guidance for a separate review workflow; that
skill is not part of the selected set. Perform ordinary review without claiming
to invoke an uninstalled skill.

The setup workflow has not been run. An issue tracker has not been selected.
Installation alone does not configure a tracker or start a requirements interview.

## Applying skills

- Use a skill when explicitly requested or when its invocation policy allows
  automatic use and the task fits its description.
- Read `.agents/skills/<skill-name>/SKILL.md` before applying an installed skill,
  then follow its relevant references. A reference to calling a "Skill tool"
  means load and apply the named skill using the available Codex mechanism.
- Adapt language-specific examples to Python and the tools actually configured
  here. A TypeScript example does not establish a TypeScript requirement.
- For `tdd`, agree with the user on the public interfaces to test before writing
  tests, as required by the upstream workflow. Reuse previously agreed scope.
- For debugging, build a runnable reproduction and verify the reported symptom
  after the fix. Prefer Python for reproduction scripts and temporary harnesses.
- Use architecture workflows when there is an actual design question or
  existing code to assess; avoid speculative abstractions.
- Do not assume an issue tracker, remote repository, deployment target, or
  external publication workflow until one is selected or requested.
- Keep generally available skills, such as visualization and OpenAI Docs,
  available for relevant requests without copying them into this project.
