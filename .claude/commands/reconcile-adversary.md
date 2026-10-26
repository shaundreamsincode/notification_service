---
name: reconcile-adversary
description: Reconcile a pending adversarial review into the main OpenSpec
  artifact(s) using a summary-then-approve gate, then mark it reconciled while
  retaining all adversarial files. Runs as the main agent and edits the main
  artifacts.
---
Load the `openspec-adversary-reconcile` skill for the reconciliation protocol.

**Input**: The argument after `/reconcile-adversary` is an optional change name (e.g. `add-organizations`). If no argument is given, resolve the change by inference — explicit argument, then the change in active use this session (continuity is the default), then the single existing change if there is only one. Only ask which change when there are multiple changes, no argument, and no session signal.

## Goal

$ARGUMENTS

## Workflow

Follow the `openspec-adversary-reconcile` skill exactly. The skill reads a `pending_reconciliation` adversarial review plus its artifact and the corresponding main artifact, summarizes the recommended changes, and proposes a concrete batch of edits to the **main** artifact(s).

It uses a **summary-then-approve** gate: all proposed edits are presented in the message, then a single `question` gate (Approve / Reject / Request changes). Only on Approve are the main artifacts edited. On Approve, the skill marks the `state.json` entry `reconciled` and retains all adversarial files (artifacts and reviews are kept for history — nothing is deleted).

This command edits the **main** artifacts, run as the main agent. It reconciles one pending entry per invocation: present the proposed edits, gate on approval, and on approval apply them and mark the entry `reconciled`.
