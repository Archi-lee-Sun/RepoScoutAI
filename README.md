# RepoScoutAI

RepoScoutAI is a personal, preference-learning agent pipeline that discovers new GitHub repositories, filters them against your interests, explains them in English, translates that explanation into Georgian, and delivers everything via Telegram with Accept/Reject actions. Accepted repositories are automatically starred on your GitHub account.

## Status

🚧 In development — `poller.py` and `state.py` (shared data classes) are implemented. Remaining agents are in design.

## Pipeline

```
Poller ─▶ Prompt-Engineer Agent ─▶ Validator ─▶ Explainer ─▶ Translator ─▶ Telegram
  │              │                    │                                      │
  │              │                    └─ if rejected: stop here              │
  │              │                                                           ▼
  │              └─ reads PreferenceMemory (history.json)          User taps Accept / Reject
  │                 once per cycle, writes one meta-prompt                   │
  │                 reused for every candidate in that cycle         ┌───────┴───────┐
  │                                                                  ▼               ▼
  └─ writes candidates.json                                  GitHub API (star)  PreferenceMemory
     tracks last-checked date per cluster (state.json)                          (history.json)
```

### Step by step

1. **Poller** — queries the GitHub Search API once per cluster (see Interest Clusters below), dedupes results across clusters by repo `full_name`, and writes the merged list to `candidates.json`. Tracks each cluster's last-checked date in `state.json` so later runs only look for repos created since the previous run.
2. **Prompt-Engineer Agent** — runs once per cycle, reads the full accept/reject history from `PreferenceMemory`, and generates one meta-prompt describing what you're currently looking for. This same meta-prompt is reused for every candidate validated in that cycle — it is not regenerated per repo, and it is not stored on `Candidate`.
3. **Validator** — takes a `Candidate` and the cycle's meta-prompt, decides accept or reject, and fills in `Candidate.is_accepted` and `Candidate.validation_reason`.
4. **Explainer** — for accepted candidates only, writes a clear English explanation of what the repo is, how it works, and example use cases. Fills `Candidate.explanation_en`.
5. **Translator** — translates the English explanation into Georgian. Fills `Candidate.explanation_ka`.
6. **Telegram Dispatch** — sends the repo link and Georgian explanation with inline `Accept & Star` / `Reject` buttons.
7. **On tap** — the webhook handler only logs the decision and, on Accept, stars the repo via the GitHub API. It does not call an LLM synchronously, so button responses stay fast.

## Data model (`state.py`)

- **`Candidate`** (dataclass) — shape of one repo record as it moves through the pipeline. Required fields (`full_name`, `url`, `description`, `stars`, `language`, `matched_clusters`) are filled by the poller; the rest (`is_accepted`, `validation_reason`, `explanation_en`, `explanation_ka`) default to `None` and are filled progressively by later steps.
- **`PollerState`** — wraps `state.json`. Per-cluster last-checked date, read/written only by the poller.
- **`PreferenceMemory`** — wraps `history.json`. Logs only your own Accept/Reject decisions (full_name, url, description, decision, reason) — never the validator's own accept/reject calls, so the feedback loop only ever learns from verified human decisions.

No database is used — this is single-user, personal-use, and JSON files are sufficient.

## Discovery

GitHub has no push mechanism for "new repo matching X" — the poller works by polling the Search API on a schedule (cron), not by listening for events.

Interest clusters, each run as its own scoped query (`in:name,description,readme,topics`, multi-word terms quoted as exact phrases, `stars:>3` as a noise floor rather than a popularity bar):

- `llm`
- `agents`
- `python_packages`
- `app_dev`
- `ux_design`
- `coding_agents`
- `prompt_engineering`

## LLM

`gemini-3.1-flash-lite` via the Google AI Studio API, used by the prompt-engineer agent, validator, explainer, and translator.

## Hosting

Oracle Cloud Always Free tier (Ampere A1 VM). The pipeline runs on a cron schedule; state files persist normally on disk.

## Requirements

### Credentials (`.env`, not committed — see `.env.example`)

- `GOOGLE_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `GITHUB_TOKEN` — Personal Access Token, `public_repo` scope (search + starring)

### Python packages

- `requests`
- `python-dotenv`
- `google-generativeai` / `langchain-google-genai`
- `python-telegram-bot`

## Project structure

```
state.py          # Candidate, PollerState, PreferenceMemory
nodes/
  poller.py        # Candidate Finder
  (validator, explainer, translator, telegram handler — in progress)
```

## Future additions (not yet built)

- RSS-based field news
- Social media listening, scoped to platforms that don't require login-state cookies