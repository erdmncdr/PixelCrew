# PixelCrew

**English** · [Türkçe](README.tr.md)

PixelCrew is a macOS app that runs the AI coding tools you already use — **Claude Code, Codex, Gemini (Antigravity CLI) and Grok Build** — as one team. You describe a task; the agents plan it, split it up, work in parallel and review each other's work. You watch all of it live in a pixel-art office: who is writing which file, which command is running, whose work is in review.

PixelCrew does not ship its own model. It drives the official CLIs on your Mac, signed in with your own subscriptions or API keys.

## Install

1. Download `PixelCrew-<version>.dmg` from the [latest release](../../releases/latest).
2. Open it and drag **PixelCrew** to **Applications**.
3. Open PixelCrew. The first launch shows **Set up your crew**.

Requirements: macOS 14 or later (Apple silicon or Intel) and Apple's Command Line Tools, which bring Python 3. If they are missing, PixelCrew offers to install them.

## Connect your agents

The setup screen has a card per agent. Connect at least one; each can use its own subscription or an API key.

| Agent | Tool it runs | Subscription sign-in | API key |
| --- | --- | --- | --- |
| Claude | Claude Code | Claude Pro / Max (`claude auth login`) | Anthropic Console key |
| Codex | Codex CLI | ChatGPT plan (`codex login`) | OpenAI platform key |
| Gemini | Antigravity CLI (`agy`) | Google account (`agy`) | Google AI Studio key |
| Grok | Grok Build | SuperGrok / X Premium+ (`grok login`) | xAI Console key |

- **Install in Terminal** opens Terminal with the tool's official install command. Antigravity CLI links to its install guide instead.
- **Sign in in Terminal** opens Terminal with the tool's sign-in command; finish it there and the card turns green by itself.
- **API key** stores the key in your macOS Keychain (never in PixelCrew's files) and hands it only to that agent's CLI. API usage is billed to your provider account. The CLI still has to be installed.
- **On the team** chooses who takes part. Work is shared among the agents that are ready; if none is, turn on **Demo** to watch a simulated run that spends nothing.

You can come back to this screen from **PixelCrew > Agents & Sign-in…** or **Settings > Sign-in & API keys…**.

## Two modes

- **Build:** your request is planned, split across the agents, implemented and reviewed. Something that isn't a task ("hi") gets a direct answer instead of a plan.
- **Ask:** your question goes to one agent or the whole team. They read the files in the folder and answer without changing anything. Follow-ups continue the same conversation.

## How a run works

1. **Planning.** A senior agent looks at the working folder and splits the request into 1–6 tasks, each with an owner, the files it touches and a done-when criterion.
2. **Plan review.** Another agent critiques the plan and fixes missing steps, wrong order or file conflicts.
3. **Approval.** With "Approve plan" on, the crew waits for you. Click an agent's name on a task to hand it to someone else.
4. **Building.** Tasks whose dependencies are done start; agents work on different tasks at the same time.
5. **Cross-review.** Each finished task is reviewed by a different agent, which can read files and run tests but cannot edit. Real problems go back to the author for a fix round.
6. **Final check.** For multi-task runs, the planner runs the tests and makes the smallest fix if the pieces don't fit together.
7. **Summary.** Duration, task results, changed files and token use.

### Roles and models

Each agent is a **senior** or a **worker** (Settings > Team; by default Claude and Codex are senior). Seniors plan, take hard and cross-cutting tasks and review; workers take well-defined, routine ones. Every job runs on a strong, standard or fast model depending on how hard it is:

| Tier | Jobs | Claude | Codex | Gemini | Grok |
| --- | --- | --- | --- | --- | --- |
| Strong | plan, plan review, final check, hard tasks | Opus | GPT-6 Astra | Gemini 3.1 Pro (high) | Grok 4.7 |
| Standard | medium tasks, chat | Sonnet | GPT-6.1 Sol | Gemini 3.1 Pro (high) | Grok 4.7 |
| Fast | easy tasks, re-reviews after a fix | Haiku | GPT-6 Luna | Gemini 3.1 Pro (low) | Grok 4.7, low effort |

You can pin a model per agent in Settings, or always use each CLI's default.

### Quota balancing

PixelCrew reads each agent's remaining usage limit (Claude and Gemini through the CLIs' own `/usage`, Codex from its session logs, Grok through the same billing call its `/usage` screen makes). An agent running low drops a model tier and gets no heavy tasks; one that is almost out hands its tasks, reviews and the planner role to the others; a job that hits the limit mid-run is taken over by another agent. Every decision is explained in the Tasks tab.

## Permissions and safety

- **Safe (default):** agents may edit files in the working folder. Claude runs only well-known developer commands; Codex, Grok and Gemini run in a sandbox that cannot write outside the folder. Planning, questions and reviews are read-only.
- **Full access:** every command runs without asking and network access is on. Use it only in folders you trust.

Even in Safe mode agents change files and run commands such as `npm` or `python`, which can execute code. Commit your work to git before running PixelCrew on an important project.

The local server listens on `127.0.0.1` only, checks the `Host` header on every request and accepts state-changing requests only as same-origin JSON. Agents start only in the folder you chose; your whole home folder or `/` can't be chosen.

## Languages

English and Turkish. PixelCrew follows your system language; change it in Settings or on the setup screen.

## Updates

PixelCrew checks this repository's latest release on launch and once a day. When a new version is out, a bar offers to update: the new DMG is downloaded, installed only if the app inside is signed by the same Developer ID team and notarized by Apple, and swapped in when PixelCrew restarts (the old copy goes to the Trash). Turn automatic checks off in Settings, or use **PixelCrew > Check for Updates…** at any time.

## Data

Settings, history and chats live in `~/Library/Application Support/PixelCrew`; the server log is `~/Library/Logs/PixelCrew/server.log`. The Help menu opens both. API keys are in the login keychain under `app.pixelcrew.PixelCrew`.

## Build from source

```bash
git clone <this repository>
cd PixelCrew
./script/build_and_run.sh            # build into dist/ and open it
./script/build_and_run.sh --install  # install into /Applications
```

The server is plain Python 3 (standard library only), so it also runs in a browser during development:

```bash
python3 pixelcrew.py                 # http://127.0.0.1:8787, data in ./data
node --test tests/office-routines.test.cjs
```

### Release

```bash
xcrun notarytool store-credentials pixelcrew   # once (an App Store Connect API key or an app-specific password)
gh auth login                                  # once, with the GitHub CLI
NOTARY_PROFILE=pixelcrew PUBLISH=1 ./script/release.sh
```

`release.sh` builds a universal app, signs it with your Developer ID and the hardened runtime, notarizes and staples the app and the DMG, and writes `dist/PixelCrew-<version>.dmg` with a SHA-256 checksum. With `PUBLISH=1` it also tags the commit, publishes the GitHub release with the DMG and the notes from `release-notes/<version>.md`, and checks that the published download matches. The version comes from `VERSION`; installed copies pick the release up through the in-app updater.

## Project layout

```
pixelcrew.py          starts the server (the app uses it too)
mac/                  macOS app (Swift): window, menus, folder panel, notifications
script/               build_and_run.sh, release.sh, check_bundle.py
server/
  app.py              HTTP server, JSON API, live event stream (SSE)
  orchestrator.py     planning, task split, parallel runs, cross-review
  chat.py             Ask mode
  agents.py           runs the claude, codex, agy/gemini and grok CLIs
  keys.py             API keys in the macOS Keychain
  config.py           settings, CLI discovery, health checks
  models.py, quota.py roles, model tiers, quota balancing
  status.py           models and remaining usage limits
  prompts.py          planner, reviewer, builder and fixer prompts
  i18n.py             server messages in English and Turkish
web/                  the UI: index.html, app.js, i18n.js, office.js (the pixel office)
```

## Troubleshooting

- **A card says "Sign-in needed" after signing in.** Press **Check again**. Claude's desktop app sign-in does not carry over to the CLI; run the sign-in from the card.
- **A tool is installed but not found.** PixelCrew looks in `~/.local/bin`, Homebrew and your login shell's `PATH`. Point it elsewhere with `PIXELCREW_CLAUDE_BIN`, `PIXELCREW_CODEX_BIN`, `PIXELCREW_AGY_BIN`, `PIXELCREW_GEMINI_BIN` or `PIXELCREW_GROK_BIN`.
- **An agent seems stuck.** **Stop** ends every agent process. Each job has a time limit (30 minutes by default, in Settings).

## License

[Apache License 2.0](LICENSE). Claude, Codex, Gemini and Grok are trademarks of their owners; PixelCrew is not affiliated with Anthropic, OpenAI, Google or xAI.
