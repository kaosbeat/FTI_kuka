# Agent Workflow

Your first task is to split every task in a logical list of TODOs, they should be split into analyse, research, implement, test and handoff tasks. 
Their order is up to you to choose. The granularity too. The goal is to have a safe resume point _after_ each task from which you can continue in case of error or adaptation. Therefore it is mandatory to after each task write a small handoff log. Only after the handoff is done, you mark this task as complete. Then insert a checkpoint and continue with the following on the todo until all done. Then you write the full handoff.
The number of TODOs per prompt is dependant on the complexity

## Mandatory Task Handoff Log

After every completed task, you MUST update:

`.kilo/task-handoff.md`

This requirement applies to every task, including:

- Adding, changing, or removing a function
- Fixing a bug
- Refactoring code
- Adding or changing tests
- Updating configuration
- Making an architectural change
- Updating documentation
- Investigating an issue, even when no code is changed

Do not consider a task complete until the handoff document has been updated.

## Task Completion Procedure

Before marking a task complete:

1. Review all files changed during the task.
2. Run the relevant tests, type checks, linters, formatters, or build commands.
3. Update documentation for any new or changed behavior.
4. If the change affects system structure, modules, dependencies, data flow, boundaries, or design decisions, update the relevant architecture documentation.
5. Create or update `.kilo/task-handoff.md`.
6. Add a new entry at the top of the file for the task just completed.
7. Clearly identify the next logical task, if one exists.
8. Only then report the task as complete.

## Required Handoff Entry

Each entry in `.kilo/task-handoff.md` must use this format:

```md
## YYYY-MM-DD — Short task title

### Status

Completed

### Summary

Describe what was implemented, changed, fixed, or investigated.

### Changes Made

- `path/to/file`: explain the change
- `path/to/another-file`: explain the change

### Documentation

- State which documentation was added or updated.
- If no documentation was needed, write: `None required — explain why.`

### Architecture

- State whether the architecture documentation was updated.
- If this was an architectural change, identify the document and summarize the update.
- If no architecture documentation was needed, write: `No architectural impact.`

### Validation

- List tests, type checks, linting, formatting, and build commands run.
- Include the result of each command.
- If a check was not run, explain why.

### Decisions and Caveats

- Record important implementation decisions, tradeoffs, limitations, or follow-up concerns.
- Write `None` if there are none.

### Next Task

Describe the next logical task to perform. If no next task is known, write:

`No immediate next task identified.`

### Instructions for the Next Task

Provide concise instructions that allow another agent to continue without reconstructing context.
