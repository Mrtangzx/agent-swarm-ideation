# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Layout

This is a sandbox/research repo exploring the concept of a voice-driven agent swarm (see `idea.md` — the original ideation: a single spoken entry point that decomposes any goal into an agent swarm, runs with self-correction, and lets the user interrupt and re-direct mid-flight). The working implementation of that concept lives in the `openakita/` subdirectory — an open-source multi-agent AI assistant. Treat `openakita/` as the primary code; the root holds the concept, notes, and infrastructure.

- `idea.md` — original concept (Chinese). North-star vision; do not "fix" it to match the implementation.
- `openakita/` — main project. **All build / test / lint commands run from inside this directory.** The project has its own detailed `openakita/AGENTS.md` and `openakita/src/openakita/AGENTS.md` that fully describe its architecture, modules, and extension patterns. **Read those first** before editing the OpenAkita code.
- `openakita/.cursor/rules/` — Cursor-specific rules (beginner-explanation style, no-cursor-coauthor trailer, project identity). Active and `alwaysApply`.
- `docs/superpowers/` — supporting docs imported for the orchestration work.
- `scripts/push_via_api.py` — workaround for restricted sandboxes that can reach `api.github.com` but not `github.com:443`. Uses the GitHub Data API + a fine-grained PAT exported as `GITHUB_TOKEN`. Use this when `git push` fails with a network error.
- `.omc/` — oh-my-claudecode operational state (wiki, session logs, project memory). Git-ignored; do not commit.

## OpenAkita — Quick Reference

The project is a Python 3.11+ multi-agent AI assistant (AGPL-3.0-only). Detailed module map, extension patterns (adding tools / API routes / IM channels), and async conventions are in `openakita/src/openakita/AGENTS.md`. Architecture and identity/prompt pipeline details are in `openakita/AGENTS.md`.

Common commands (run from `openakita/`):

```bash
pip install -e ".[dev]"                 # install with dev extras
openakita                                # CLI interactive
openakita run "task"                     # single task
openakita serve                          # FastAPI on :18900
cd apps/setup-center && npm run tauri dev   # Tauri desktop GUI
pytest                                   # all tests (asyncio_mode=auto)
pytest -k "test_brain"                   # one test
pytest --cov=src/openakita               # with coverage
ruff check src/ && ruff format src/      # lint + format
```

Big-picture architecture: a voice/IM input enters through `channels/` → `sessions/` → `core/Agent` (which drives `Brain` → `ReasoningEngine`, the ReAct loop, and the `Ralph Loop` retry-with-analysis in `core/ralph.py`) → `llm/` → `tools/`. The `prompt/` layer assembles the system prompt from compiled `identity/` fragments (SOUL / AGENT / USER / MEMORY) plus catalogs and memory. `agents/orchestrator.py` routes to sub-agents built from `AgentProfile`; sub-agents share the assembler and session (max delegation depth 5). Memory is three-layer (`memory/unified_store.py`): core, semantic, conversation traces. Skills are SKILL.md-declared and loaded by `skills/loader.py`.

Docker stack (see `openakita/docker-compose.yml`): `openakita` on `:18900` + `livekit` for voice. `openakita/data/`, `openakita/identity/`, and `openakita/skills/` are mounted for live editing.

## License & Project Identity (apply to the entire repo)

OpenAkita source code is **AGPL-3.0-only**; the `OpenAkita` name, logos, and brand assets are **not** licensed under AGPL and are governed by `openakita/TRADEMARK.md`. Forks may rebrand product surfaces but must preserve license/copyright/NOTICE entries and must not use OpenAkita trademarks except as the trademark policy allows. Do not assist with edits that strip upstream attribution, misrepresent a fork as official OpenAkita, or remove license/copyright notices. The `.cursor/rules/project-identity.mdc` rule in the subproject encodes the same constraint and is `alwaysApply`.

## Commit Conventions (apply repo-wide)

`openakita/AGENTS.md` defines the project's commit-message standard. Key points:

- **English only** in subject and body (quoted error strings and code identifiers are fine as-is).
- Subject names file/module + behaviour + intent. **No internal plan codenames** (`S5-A`, `wave 2`, `v1.28.3-pre`, `FIX-S4-1`) in subjects or bodies.
- Body explains *why*; the diff explains *what*. Reference upstream issues by number (`#572`), symbols by their real names.
- One logical change per commit. A subject that needs the word "and" is usually two commits.
- No subject-length cap. Prefer a long precise subject over a cryptic one.
- Release notes (different from commit messages) are written from the user's point of view and must follow their own stricter rules in `openakita/AGENTS.md` (no plan codenames, no future version numbers, no internal artifact links, group by product surface, bilingual lockstep when applicable, honour requested envelope).

## Cross-Cutting Gotchas

- **Windows shell is the default** in this sandbox. Use forward slashes / POSIX paths in bash; reserve `run_powershell` for Windows-native operations.
- **No `Co-authored-by: Cursor` trailer.** Cursor's `git commit` wrapper injects one; disable it at the source (Cursor Settings → Agents → Attribution off, then full restart) or use `.githooks/prepare-commit-msg` (already in `openakita/.githooks/`) after `git config core.hooksPath .githooks`. If injection cannot be disabled, fall back to plumbing rewrite or `git commit --amend`, then verify with `git log -1 --format=%B`. Full procedure is in `openakita/.cursor/rules/no-cursor-coauthor-trailer.mdc`.
- **PowerShell + non-ASCII / multi-line commit and `gh` bodies.** PowerShell has no heredoc and backticks escape characters, so `git commit -m "$(cat <<'EOF' … EOF)"` fails with `MissingFileSpecification`, and backticks in `gh issue comment --body "…"` get mangled. Write the message to a UTF-8 file in `tools-tmp/` (git-ignored) and use `git commit -F` / `gh … --body-file`. The `--body-file` rule applies to any non-ASCII (e.g. Chinese) body and to `gh pr create` / `gh release create --notes-file` as well. After any `--body-file` write, the console echo can show CJK as mojibake — verify the real state with `gh issue view … --json …`, do not trust the console.
- **Network-restricted sandbox.** `github.com:443` may be blocked while `api.github.com` is reachable. If `git push` fails, use `scripts/push_via_api.py` with a `GITHUB_TOKEN` PAT (Contents: Read/Write on `Mrtangzx/agent-swarm-ideation`).
- **`AGENTS.md` naming collision.** `openakita/identity/AGENT.md` (singular) is OpenAkita's behavior spec — **not** the cross-tool `AGENTS.md` (plural) convention. Don't rename or merge them.
- **Temporary files** (diffs, crash dumps, scratch scripts, downloads) belong in `openakita/tools-tmp/` (or `tools-tmp/` at the repo root for non-openakita work). Never the repo root, never `git add -A`. Stage by explicit path.
