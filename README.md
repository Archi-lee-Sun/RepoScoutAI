# RepoScoutAI

RepoScoutAI is a single-user, evidence-driven GitHub discovery agent. It finds repositories across a fixed set of engineering interest areas, evaluates their relevance and visible technical substance, and sends a concise Georgian-language summary to Telegram for a human decision. Accepting a recommendation stars the repository on the configured GitHub account.

The project is an asynchronous-by-stage automation pipeline, not an open-ended conversational chatbot. Gemini makes structured, bounded decisions over repository evidence; the user remains the final authority on what to star. The system uses GitHub REST APIs, Gemini (`gemini-3.1-flash-lite`), and Telegram, with local JSON files for durable state.

## What it does

- **Discovers candidates** through interest-cluster searches or separate weekly and monthly trending searches.
- **Filters in two stages.** A metadata-only validator checks plausible relevance. Candidates that pass have their README, a shallow file-tree view, and up to three selected source or configuration files fetched for a second, evidence-based selection.
- **Limits expensive writing stages.** Selector-approved repositories are ranked and capped at eight finalists by default. Gemini writes a concise English explanation from the supplied artifacts, then translates it into Georgian.
- **Requests a human decision.** Telegram messages include inline **Accept** and **Reject** buttons. Accept stars the repository; either choice is recorded as explicit preference feedback.
- **Reviews previously starred repositories.** A separate cleanup job evaluates repositories with no push in at least 21 days and removes a star only when the supplied evidence indicates the core project is unfinished. Inactivity alone is not considered evidence of abandonment.

## How a discovery run works

```text
GitHub search
    -> metadata relevance gate (Gemini)
    -> README, file tree, selected files (GitHub)
    -> evidence-based selector and score (Gemini)
    -> rank and cap finalists
    -> English explanation -> Georgian translation (Gemini)
    -> Telegram recommendation with Accept / Reject buttons
    -> GitHub star on Accept; decision added to preference history
```

### Discovery sources

`discover` searches seven configured interest clusters: LLMs, agents, Python packages, app development, UX/design, coding agents, and prompt engineering. It searches for repositories created since each cluster's last successful checkpoint. `trending-week` and `trending-month` are independent sources that search recently created repositories ordered by stars. All sources require at least five stars and a non-empty description; repositories already marked seen are deduplicated across sources.

### Evidence and model decisions

The first Gemini stage sees only repository name, URL, description, star count, language, and matching clusters. Its permissive relevance decision determines whether deeper evidence should be fetched; it does not claim to judge code quality.

The fetch stage requires a README of at least 400 characters. It supplies at most 8,000 README characters, a shallow listing of up to 50 tree entries, and up to three selected files, each limited to 3,000 characters. The selector returns a structured accept decision, a 0–100 score, and a reason based on the available evidence. Missing, truncated, or sparse materials limit what the model can conclude.

The English explainer and Georgian translator run only for the ranked finalists (eight by default). Explanations are grounded in the README and selected files. Repository content is treated as untrusted input in the model prompts; embedded instructions are not followed. LLM stages use structured outputs, and transient remote failures are retried. Candidate-level temporary failures remain eligible for a later run.

### Preference feedback

`history.json` records the user's Telegram Accept/Reject choices, not the pipeline's internal judgments. Before discovery evaluation, the prompt-engineering stage uses that history together with the configured interests to build preference context for the validator and selector. This lets explicit user behavior refine recommendations without treating a single choice as a universal rule.

### Checkpoints and retry behavior

Search progress is tracked independently for each interest cluster and trending source in `state.json`. A source checkpoint advances only when its candidates have completed processing; candidates with temporary failures keep their source checkpoint open for retry. Completed candidates are also recorded in the seen set to prevent rediscovery. Empty searches and candidates removed by intentional local filters can advance without downstream processing.

State writes use atomic JSON replacement and file locks so the CLI jobs and bot can safely share local state. A scheduled-job lock prevents overlapping CLI jobs. Telegram callback entries are persisted in `pending.json`, claimed atomically to prevent duplicate processing, and pruned after 30 days.

## Requirements

- Python 3.10 or newer
- A Google AI API key for Gemini
- A GitHub token with repository read access and permission to star and unstar repositories
- A Telegram bot token and destination chat ID

Install runtime dependencies from the repository root:

```powershell
python -m pip install -r requirements.txt
```

Create a `.env` file in the repository root using `.env-example` as a template:

| Variable | Purpose |
| --- | --- |
| `GOOGLE_API_KEY` | Gemini model access |
| `GITHUB_TOKEN` | GitHub search, repository contents, starring, and unstarring |
| `TELEGRAM_BOT_TOKEN` | Telegram bot authentication |
| `TELEGRAM_CHAT_ID` | Destination chat for recommendations |
| `MAX_TELEGRAM_RECOMMENDATIONS` | Maximum finalists per pipeline run; defaults to `8` |

Keep `.env` private. The chat ID must be configured manually; the bot does not discover it automatically.

## Run jobs

Run one job from the repository root:

```powershell
python main.py discover
python main.py trending-week
python main.py trending-month
python main.py cleanup
```

Run the Telegram bot in a separate, long-lived process so it can receive button callbacks:

```powershell
python nodes/bot.py
```

The CLI runs one job and exits. For unattended operation, configure an operating-system scheduler for the desired jobs and keep the bot process running with your chosen service manager. Scheduling and service installation are outside this repository.

## Local state files

These files are created in the repository root as the application runs and are excluded from Git:

| File | Contents |
| --- | --- |
| `state.json` | Per-source search checkpoints and seen repositories |
| `history.json` | Explicit Telegram Accept/Reject decisions used as preference feedback |
| `pending.json` | Outstanding Telegram callbacks and their processing claims |
| `repo_status.json` | Repositories starred through the bot and cleanup status |

Lock files are created alongside state files as needed. Back up the JSON files if you need to preserve history and checkpoints across machines.

## Development

Install the development requirements, then run the test suite:

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
```
