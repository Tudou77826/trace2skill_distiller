# Trace2Skill Memory Review Roadmap

## Product Direction

Trace2Skill should become a session review and memory consolidation tool for coding agents. Its primary job is not to turn every conversation into a `SKILL.md` file. Its job is to replay human-AI work sessions, identify durable learning signals, preserve evidence, and promote only useful memories into future agent context.

The main product surface should be a local GUI, not a large command tree. The GUI should let users choose sessions from multiple Agent sources, run extraction on the selected sessions, inspect high-value memories, and review uncertain items. CLI commands should remain available for automation and debugging, but the default path should feel like a service: select sessions, extract value, review, install.

For non-technical users, the preferred distribution should be a portable Windows executable. `trace2skill-gui.exe` should start the local GUI and open the browser without requiring a Python installation, `uv`, or command-line setup. The CLI executable can remain as an advanced artifact.

This direction follows the same product distinction used by Claude Code memory: human-written persistent instructions are different from auto-memory learned from corrections, preferences, build commands, debugging insights, and project facts. Trace2Skill should specialize in the auto-memory side while still being able to export promoted rules into agent-readable instruction or skill files.

The recommended user experience is:

```bash
trace2skill dream
trace2skill dream --project my-project
trace2skill dream --session <session-id>
trace2skill dream --project my-project --limit 5
trace2skill dream --project my-project --all
trace2skill memory stats --project my-project
trace2skill review --project my-project
trace2skill memory review --project my-project
trace2skill context --project my-project
trace2skill memory show <memory-id> --project my-project
trace2skill memory edit <memory-id> --project my-project --action "..."
trace2skill memory archive <memory-id> --project my-project
trace2skill memory confirm <memory-id> --project my-project
```

Advanced commands such as `run`, `inspect`, `sessions`, and `config` should remain available, but they should not be required for normal review.

`dream` should default to incremental recent-session review so a routine pass is fast and predictable. It skips already processed sessions, then reviews the latest remaining sessions. Users can raise or lower the window with `--limit`, or pass `--all` when they intentionally want to reprocess older sessions.

## Memory Types

The review pipeline should extract multiple memory categories instead of a single list of generic rules.

| Type | Purpose |
| --- | --- |
| `USER_PREFERENCE` | Stable user tastes, habits, communication preferences, and disliked behaviors. |
| `STANDING_REQUIREMENT` | Rules the assistant should keep following across sessions. |
| `REPO_FACT` | Concrete repository facts: paths, commands, architecture, data shapes, configuration, and constraints. |
| `WORKFLOW_PATTERN` | Repeatable investigation, implementation, verification, or release patterns. |
| `KNOWLEDGE_DISCOVERY` | Non-obvious technical or domain knowledge discovered while working. |
| `CORRECTION` | Wrong assumptions that were corrected and should not be repeated. |
| `TOOL_FEEDBACK` | Feedback about skills, plugins, prompts, CLI commands, or model behavior. |
| `PITFALL` | Failure modes, time-wasters, and avoidable mistakes. |
| `OPEN_QUESTION` | Uncertainty worth verifying in a later review. |

## Review Quality Bar

A memory item is useful only when it is specific, evidenced, and reusable.

Each memory should include:

- A concrete action or fact.
- A scope such as `user-specific`, `repo-specific`, `project-specific`, `tool-specific`, or `general`.
- A confidence score.
- Evidence from successful or failed trajectories.
- A condition when the memory applies only in a particular situation.

The system should reject or down-rank memories that are vague, unsupported, duplicated, contradicted, or only useful inside one transient task. A memory should enter `agent-context.md` only when it is active, high-confidence, and either evidence-backed or manually confirmed.

Contradictory memories should not enter `agent-context.md` automatically. When a new memory appears to conflict with an active memory in the same type and scope, it should be marked `review`, linked with `conflict_with`, and surfaced in the review queue.

## Consolidation Loop

A mature review tool should run a loop similar to memory consolidation:

1. Capture session traces from the configured source.
2. Segment the session into coherent intents.
3. Extract memory candidates from each segment.
4. Cluster candidates by topic and memory type.
5. Merge with existing memory while preserving source evidence.
6. Mark contradictions, weak confidence, and open questions.
7. Promote strong memories into `memory.md`, `SKILL.md`, or agent-specific context.
8. Keep low-confidence items in a review queue.
9. Re-score older memories as new sessions confirm or contradict them.

## Output Artifacts

The default artifact should be `memory.md`, not a skill article.

It should contain:

- Executive summary of the review.
- Memory quality summary with an explainable readiness score.
- Memory map by type.
- Evidence-backed memory items.
- Review queue for open questions and weak memories.
- Source session references.

The memory output layer should also maintain machine-readable and agent-ready artifacts:

- `memory_store.json`: canonical persistent store used for deduplication, evidence merging, confidence updates, and review state.
- `agent-context.md`: compact high-confidence memory intended for direct model injection.
- `repo-facts.md`: repository-specific facts, workflows, corrections, and pitfalls.
- `user-profile.md`: stable user preferences and standing requirements.
- `SKILL.md`: promoted procedural knowledge only, when the memory is actionable enough to become a skill.
- HTML review report with filtering by type, confidence, and source session.

## Agent Context Installation

A review tool becomes useful when the next agent session can actually consume the distilled memory.

The near-term Claude Code integration should keep project memory explicit and auditable:

- `trace2skill gui` starts the local GUI for choosing sessions and reviewing extracted memory.
- `trace2skill-gui.exe` is the intended no-install Windows entry point for the same workflow.
- `trace2skill dream --project <name>` writes `agent-context.md` from confirmed, evidence-backed memories.
- After a non-preview `dream`, the CLI should show the same next-action panel as `memory next` so users immediately know what was learned and what still needs review.
- `trace2skill dream --project <name> --install-context` runs the normal review and immediately installs the generated context into the current repository.
- `trace2skill memory next --project <name>` shows the current readiness score, next actions, and highest-priority review items.
- `trace2skill review --project <name>` and `trace2skill memory review --project <name>` let the user inspect weak memories before promotion.
- `trace2skill memory install-context --project <name>` writes `trace2skill-memory.md` into the current repository and adds a stable import marker to `CLAUDE.md`.
- Re-running `dream --install-context` or `memory install-context` should refresh the imported file and update the import line without duplicating it.

This keeps generated memory separate from hand-written project instructions, while still making it available through Claude Code's project memory import mechanism.

## Evaluation

The tool should be judged by whether future sessions improve.

Useful evaluation checks:

- Does each review make clear how much memory is agent-ready versus still needing evidence or human review?
- Does a future assistant avoid a previously identified mistake?
- Does it follow the user's stated preferences without being reminded?
- Does it recall repository facts accurately?
- Does it cite the source memory or session evidence when uncertain?
- Does it ask to verify weak memories instead of treating them as facts?
- Does it avoid promoting one-off details into global behavior?

## Near-Term Implementation Plan

1. Make `trace2skill dream` the recommended entry point.
2. Default output to `memory_md`.
3. Require the distillation prompt to emit typed `memory_items`.
4. Render a consolidated `memory.md` with evidence and a review queue.
5. Maintain `memory_store.json` as the canonical persistent memory store.
6. Generate `agent-context.md`, `user-profile.md`, and `repo-facts.md` from the canonical store.
7. ~~Keep `skill_md` and `knowledge_md` as optional formats.~~ — **Removed.** Only `memory_md` is supported now; the other two formatters were dropped to focus on a single, fully-designed review output. The three derived `.md` files (`agent-context.md` / `user-profile.md` / `repo-facts.md`) now accept user-configurable destination paths and append to an already-existing target file.
8. Add tests for output format routing, the `dream` command, context export, review filtering, and memory rendering.
9. Add confidence decay and confirmation counts for repeated memories.
10. Add non-interactive memory governance commands for showing, editing, archiving, restoring, and confirming memory items.
11. Add an interactive review mode for accepting, editing, rejecting, or promoting memories.
12. Add memory conflict detection so contradictory memories are queued before they reach agent context.
13. Add memory health metrics for agent-ready, review, archived, conflict, and missing-evidence counts.
14. Add a Claude Code project-memory installer that imports generated agent context without duplicating hand-written `CLAUDE.md` content.
15. Add an explainable memory readiness score to the report and CLI stats.
16. Add a `memory next` command that turns memory quality metrics into an actionable review plan.
17. Show the actionable review plan automatically after successful non-preview `dream` runs.
18. Add a local GUI that lets users select specific historical sessions before extraction.
19. Hide lower-level CLI commands from the default help so the product surface centers on GUI, dream, and memory review.
20. Add a PyInstaller GUI executable target for a no-install Windows build.
21. Add richer semantic conflict resolution using an LLM judge for cases that simple polarity checks cannot classify.
