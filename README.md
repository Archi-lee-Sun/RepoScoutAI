# RepoScoutAI

RepoScoutAI is a personal agent that discovers GitHub repositories, filters them with Gemini, explains and translates promising projects into Georgian, and sends them to Telegram for Accept/Reject decisions. Accepting a repository stars it on GitHub.

## Run locally

Use Python 3.10–3.14. Create a `.env` file in the repository root with the four credentials listed below, then install dependencies:

```powershell
python -m pip install -r requirements.txt
```

Run one discovery job manually:

```powershell
python main.py discover
python main.py trending-week
python main.py trending-month
python main.py cleanup
```

Run the Telegram bot in a separate terminal so it can receive button callbacks:

```powershell
python nodes/bot.py
```

The repository provides CLI jobs and the long-polling bot process. It does not install an operating-system scheduler or service manager; configure those separately for unattended operation. `TELEGRAM_CHAT_ID` must be set manually.

## Credentials

Keep these values in `.env` and do not commit that file:

- `GOOGLE_API_KEY` for Gemini.
- `GITHUB_TOKEN` for repository search, contents, and starring. It needs permission to star repositories.
- `TELEGRAM_BOT_TOKEN` for the bot.
- `TELEGRAM_CHAT_ID` for the destination chat.

`.env-example` is a blank template.

## Pipeline

`main.py` selects a discovery or cleanup job. Discovery candidates pass through validation, GitHub README/tree/file fetching, selection, explanation, translation, and Telegram dispatch. The Telegram bot handles Accept/Reject callbacks separately. JSON files in the repository root persist search checkpoints, completed repositories, pending callbacks, user feedback, and starred-repository status.

Normal discovery and each trending job have independent checkpoints. A checkpoint advances only after its candidates have completed; transient API, LLM, and Telegram delivery failures remain eligible for retry. Repository state files and lock files are runtime data and are excluded from Git.

## Dependencies and tests

Runtime dependencies are listed in `requirements.txt`. To install the test runner too, use:

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
```
