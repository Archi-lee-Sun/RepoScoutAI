# RepoScoutAI

RepoScoutAI is a personal, preference-learning agent pipeline that discovers new GitHub repositories, filters them against your interests, summarizes and translates them into Georgian, and delivers them via Telegram with Accept/Reject actions. Accepted repositories are automatically starred on your GitHub account.

## Status

🚧 In development — design stage complete, implementation in progress.

## Architecture

```
┌───────────────────┐     ┌──────────────────┐     ┌───────────────────────┐
│ Preference Memory  │ ──> │ Candidate Finder  │ ──> │ Validator Agent        │
│ (history.json)     │     │ (GitHub Search)   │     │ (send / skip decision) │
└───────────────────┘     └──────────────────┘     └───────────┬───────────┘
         ▲                                                      │ (accepted candidates only)
         │                                                      ▼
┌───────────────────┐     ┌──────────────────┐     ┌───────────────────────┐
│ GitHub API         │ <── │ User Interaction  │ <── │ Gemini 3.1 Flash-Lite  │
│ (Star Repo)        │     │ (Accept / Reject) │     │ (Summary + Georgian)   │
└───────────────────┘     └──────────────────┘     └───────────────────────┘
```

### Pipeline steps

1. **Candidate Finder** — searches GitHub for new/trending repositories using the authenticated GitHub API.
2. **Prompt-Engineer Agent** — reads the accept/reject history (with reasons) from `history.json` and writes/refines the prompt used by the Validator Agent for the next run.
3. **Validator Agent** — takes the refined prompt and each candidate repository, decides whether it matches current interests, and decides send or skip.
4. **Gemini 3.1 Flash-Lite (single pass)** — for accepted candidates, produces a clear breakdown with small examples and a Georgian translation in one call.
5. **Telegram Dispatch** — sends the repo link and breakdown to Telegram with inline `Accept & Star` / `Reject` buttons.
6. **User Interaction** — your button tap is recorded.
7. **GitHub API** — on Accept, the repository is starred on your GitHub account via a Personal Access Token.
8. **Preference Memory** — the accept/reject decision (with reason) is appended to `history.json`, feeding back into step 2 for the next cycle.

## Memory

No external database. A local `history.json` file stores the accept/reject log (append-only) and is the only persisted state. This is a personal, single-user project, so no SQL/vector database is used.

## LLM

`gemini-3.1-flash-lite` via the Google AI Studio API, used for both the summary/breakdown and the Georgian translation in a single call.

## Hosting

Oracle Cloud Always Free tier (Ampere A1 VM), chosen so that `history.json`, the Telegram bot process, and scheduled runs (via crontab) all persist normally on disk without extra workarounds.

## Requirements

### Credentials (`.env`, not committed — see `.env.example`)

- `GOOGLE_API_KEY` — Google AI Studio API key
- `TELEGRAM_BOT_TOKEN` — from @BotFather
- `TELEGRAM_CHAT_ID` — your Telegram chat ID
- `GITHUB_TOKEN` — Personal Access Token with `public_repo` scope (used for authenticated GitHub search and for starring repositories)

### Python packages

- `google-generativeai` / `langchain-google-genai`
- `python-telegram-bot`
- `PyGithub` (or `requests` against the GitHub REST API)
- `python-dotenv`

## Future additions (not yet built)

- RSS-based field news
- Social media listening (via Agent Reach), scoped to platforms that don't require login-state cookies