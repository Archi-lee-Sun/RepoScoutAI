RepoScoutAI — Project Context for Codex

This document exists to bring you (Codex) fully up to speed on RepoScoutAI so you can pick up where development left off, debug issues, and continue implementation without needing the project re-explained. Read this in full before touching any code.

1. What this project is

RepoScoutAI is a personal, single-user agent pipeline that:

Discovers new GitHub repositories matching a fixed set of interest clusters.
Filters them through two successive LLM judgment stages, informed by the user's real accept/reject history.
Writes an English explanation of each surviving repo, then translates it into Georgian.
Sends the Georgian explanation to the user via a Telegram bot, with inline Accept / Reject buttons.
On Accept, stars the repo on the user's real GitHub account and records it for future maintenance.
On a separate weekly schedule, reviews previously-starred repos and unstars ones that show concrete evidence of having been abandoned mid-build.

This is not a SaaS product. There is no database, no multi-user concept, no auth beyond a few API tokens in .env. State is persisted entirely as JSON files on disk. The whole thing runs as scheduled scripts (cron) plus one always-on process (the Telegram bot) on a single Oracle Cloud Always Free VM.

Design philosophy the user cares about and that must be preserved in any fix:

The system should never punish a repo just for being early-stage, small, unstarred, or quiet — most discovered repos are days old by construction, and most cleanup candidates are silence-flagged, not necessarily dead.
Every LLM decision stage must be able to say "not enough evidence" rather than inventing justification.
Any text fetched from GitHub (READMEs, file contents) is untrusted external content — every prompt that consumes it explicitly instructs the model to treat embedded instructions as data, never as commands, and to treat prompt-injection attempts as a negative signal on the repo itself.
history.json (the accept/reject log) must only ever be written by the user's own real button taps — never by an agent's own internal decision. This is what keeps it a trustworthy taste signal for the Prompt-Engineer agent.
2. Tech stack
Language: Python.
LLM: gemini-3.1-flash-lite, called via langchain_google_genai.ChatGoogleGenerativeAI, using the current google-genai SDK path (explicitly not the deprecated google-generativeai package).
Structured output: every LLM-calling stage defines a small pydantic.BaseModel and calls llm.with_structured_output(SomeModel) once at module load, not per call.
Retries: every single-item LLM call function is wrapped with tenacity.retry, retrying only on google.api_core.exceptions.ResourceExhausted and GoogleAPICallError, 3 attempts, exponential backoff (multiplier=2, min=4, max=16), reraise=True.
GitHub access: plain requests calls to the REST API (api.github.com), authenticated with a personal access token via Authorization: Bearer ....
Telegram: aiogram (async), long polling (no webhook, no public URL needed).
Persistence: flat JSON files via small hand-written wrapper classes in state.py — no ORM, no database.
Hosting: Oracle Cloud "Always Free" VM, chosen specifically because Render's free tier has no persistent background worker (only spin-down web services), and this project needs a process that runs continuously (the bot) plus real cron.
Scheduling: real crontab entries for the periodic jobs; a systemd service for the always-on bot.

Environment variables (.env):

GOOGLE_API_KEY — for Gemini calls.
TELEGRAM_BOT_TOKEN — the bot's token.
TELEGRAM_CHAT_ID — the user's chat ID. Known gap: this was intended to be auto-captured from the user's first incoming Telegram message, but that capture handler was never actually implemented. Currently this must be set manually (e.g. by messaging the bot once and reading the chat ID off Telegram's getUpdates API) or dispatch will silently no-op.
GITHUB_TOKEN — needs read access (for search/contents/trees) and the "Starring" write scope (classic token: public_repo or repo; fine-grained token: explicit Starring write permission). Without the write scope, star_repo/unstar_repo fail silently into logged exceptions.
3. Directory layout
RepoScoutAI/
├── state.py                  # all persistence classes + the Candidate dataclass
├── prompts.py                 # every get_..._prompt() function, one per LLM stage
├── pipeline.py                 # (or graph.py) — orchestrates one discovery run
├── main.py                     # CLI entrypoint, dispatches to the right job via argparse
├── bot.py                      # always-on Telegram callback handler (accept/reject taps)
├── requirements.txt
├── .env / .env-example
├── .gitignore                  # excludes venv/, __pycache__/, .env, and all generated JSON state
└── nodes/
    ├── poller.py                # normal cluster-based discovery (GitHub Search API)
    ├── trending_poller.py       # secondary discovery source: most-starred repos this week/month
    ├── prompt_engineer.py       # builds the "meta-prompt" (user's taste) from history.json
    ├── validator.py             # stage 1 filter — metadata only (name/desc/stars/language)
    ├── github_client.py         # ALL GitHub REST calls live here, including the fetcher batch step
    ├── selector.py               # stage 2 filter — real evidence (README/tree/files)
    ├── explainer.py              # writes the English explanation
    ├── translator.py             # translates English explanation → Georgian
    ├── telegram_dispatch.py      # sends candidates to Telegram with Accept/Reject buttons
    └── cleanup.py                 # weekly: reviews starred repos, unstars abandoned ones

Import convention (must be followed for any new file): every file under nodes/ starts with

python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

followed by flat imports of root-level modules (from state import Candidate, from prompts import get_selector_prompt), never package-qualified imports like from nodes.github_client import .... This was an actual bug once (cleanup.py briefly used from nodes.github_client import ... and had to be fixed to the flat form) — preserve the flat convention in anything new.

4. The Candidate object — the spine of the whole pipeline

Defined in state.py as a @dataclass. Every pipeline stage receives a list[Candidate], mutates fields on each candidate in place, and returns the same list. Nothing is ever removed from the list mid-pipeline — stages "reject" a candidate by setting a boolean field to False/leaving something None, and the next stage's batch function is responsible for skipping candidates that didn't pass the previous stage. This means the orchestrator (pipeline.py) never needs branching logic — it's a flat sequence of candidates = stage_batch(candidates, ...) calls.

python
@dataclass
class Candidate:
    full_name: str
    url: str
    description: str
    stars: int
    language: str | None
    matched_clusters: list[str]

    is_accepted: bool | None = None          # set by validator.py
    validation_reason: str | None = None      # set by validator.py

    readme: str | None = None                 # set by github_client.fetch_batch
    tree_text: str | None = None              # set by github_client.fetch_batch
    code_files: dict[str, str] = field(default_factory=dict)  # set by github_client.fetch_batch
    selector_accepted: bool | None = None     # set by selector.py (or by fetch_batch on README failure)
    selector_reason: str | None = None        # set by selector.py

    explanation_en: str | None = None         # set by explainer.py
    explanation_ka: str | None = None         # set by translator.py

Critical: code_files uses field(default_factory=dict) — a plain = {} default on a dataclass is a shared-mutable-default bug (every instance would share one dict).

5. The pipeline, stage by stage
5.1 Poller (nodes/poller.py) — run_poller() -> list[Candidate]

Queries GitHub's Search API once per interest cluster (7 fixed clusters: llm, agents, python_packages, app_dev, ux_design, coding_agents, prompt_engineering), each with its own keyword list. Multi-word keywords get exact-phrase-quoted in the query. Tracks a per-cluster "last checked" timestamp in state.json (via PollerState) so each run only asks for repos created since the last successful check for that cluster. Dedupes across clusters by repo URL, merging matched_clusters when a repo hits more than one cluster's search. Filters out repos without a description or fewer than 5 stars; both the GitHub query and local filter enforce this minimum. Also checks and updates a global cross-source "seen" set (PollerState.is_seen / mark_seen_batch) so a repo already discovered once (by this poller or by the trending poller) is never reprocessed. Returns list[Candidate] — not dicts; an earlier bug converted the return value with dataclasses.asdict(), which broke every downstream .attribute access — this must stay as plain Candidate objects.

5.2 Trending Poller (nodes/trending_poller.py) — poll_trending(window: "week" | "month") -> list[Candidate]

A second, independent discovery source, added to catch high-signal repos the cluster-keyword search might miss. Queries the same Search API but sorted by stars descending, filtered by created:>=<cutoff> (7 or 30 days back), tagging candidates with matched_clusters=["trending_week"] or ["trending_month"]. Uses the same global PollerState seen-set as the normal poller, in both directions — critical, because without this a repo found by one source could be re-sent after already being rejected via the other. Important: candidates from this source are fed into the exact same full pipeline as normal candidates (validator → fetcher → selector → explainer → translator) — there is no shortcut for high-star repos. Being popular does not mean skipping quality evidence checks.

5.3 Prompt-Engineer Agent (nodes/prompt_engineer.py)

Runs once per pipeline invocation (not once per candidate). Reads the full accept/reject history from history.json (via PreferenceMemory), formats it, and calls the LLM to produce one "meta-prompt" describing the user's current taste. This meta-prompt is reused for every candidate that run — it is not regenerated per candidate. Design principle: the user's real behavior (what they actually accept/reject) is a stronger signal than their originally stated interests once real history exists, but a pattern should only be treated as real once it repeats — not overfit to a single data point. On a cold start (empty history.json), this needs to degrade gracefully — verify this was actually tested, it's flagged as an open risk.

5.4 Validator (nodes/validator.py) — stage 1 filter

Sees only cheap metadata: full_name, url, description, stars, language, matched_clusters. No README, no code. Decides is_accepted: bool + validation_reason: str via structured output (ValidatorDecision pydantic model). Explicit rule baked into its prompt: must not reject a repo purely for being early-stage or unfinished — since repos are discovered right at creation, this is the norm, not the exception. validate_batch(candidates, meta_prompt) loops all candidates, wraps each call in try/except + logger.exception, so one bad candidate never stops the batch — this try/except-per-item pattern is used identically in every subsequent LLM stage.

5.5 Fetcher (lives inside nodes/github_client.py, not a separate file) — fetch_batch(candidates) -> list[Candidate]

Runs only on candidates where is_accepted is True. For each: fetches the README (get_readme), drops the candidate (sets selector_accepted=False, selector_reason="README missing or too short", does not raise) if the README is missing or under MIN_README_CHARS (400) characters after stripping whitespace. Otherwise stores the README (truncated to MAX_README_CHARS=8000) and fetches the file tree (get_tree, via git/trees/HEAD?recursive=1 — HEAD resolves to the repo's actual default branch with no extra API call). format_tree_text trims this to top-level + one level down, folders marked with a trailing /, capped at MAX_TREE_ENTRIES=50 lines. pick_files selects up to 3 file paths to actually fetch content for, using CONTENT_EXTENSIONS (deliberately includes both code extensions like .py/.js and text/prompt formats like .md/.yaml/.json/.txt/.toml — repos discovered via the prompt_engineering and agents clusters are often not code at all, just prompt/config collections), excluding SKIP_DIRS (vendored deps, build output, tests, migrations, .github) and known noise filenames (README/LICENSE/CHANGELOG duplicates, lockfiles). Ranking is (has_prompt_keyword, size) descending — files whose path contains words like prompt/system/agent/template are preferred over plain largest-file, so prompt-repo content doesn't lose to an incidental large data file. Fetched file contents are stored in candidate.code_files (path → content, each truncated to MAX_FILE_CHARS=3000).

github_client.py also holds every other GitHub REST wrapper used anywhere in the project: get_repo_info (repo metadata incl. pushed_at), star_repo / unstar_repo (PUT/DELETE on /user/starred/{full_name}). All follow the same pattern: module-level HEADERS dict with the bearer token, timeout=15, try/except returning a safe default ({}, [], "", or False) with logger.exception on failure — never let a single failed GitHub call crash a batch run.

5.6 Selector (nodes/selector.py) — stage 2 filter

Runs on candidates that survived the fetcher (has a real README). Sees README + trimmed file tree + up to 3 key file contents + the candidate's metadata. Decides accept: bool + calibrated score (0?100) + reason via structured output (SelectorDecision). The score weighs user-interest fit, practical technical value, substantiated technical depth, novelty, and evidence quality; stars are only a weak tie-breaker. System prompt built by get_selector_prompt(meta_prompt) in prompts.py, which takes the user's taste meta-prompt and substitutes it into a fixed placeholder (<<TASTE_META_PROMPT>>, deliberately not {}-style formatting, since the meta-prompt text itself might contain literal braces) inside a larger fixed instruction template. The prompt explicitly: (a) instructs the model to determine from the files shown whether this is a "code repo" or a "prompt/agent-content repo" and apply the matching evaluation lens; (b) treats the README as marketing claims to be verified against the actual files, not taken at face value; (c) explicitly tells the model fetched content is untrusted and to ignore any embedded instructions, treating an injection attempt as a negative signal; (d) forbids rejecting purely for low stars/short README and forbids accepting purely for matching stated interests — no single signal should dominate. select_batch filters to candidates where is_accepted is true, readme is present, and selector_accepted is None (so it skips ones the fetcher already rejected for missing README, and skips re-processing already-decided ones).

5.7 Explainer (nodes/explainer.py)

Runs on candidates where selector_accepted is True (deliberately checked as is True, not just "not False", so a candidate whose selector call failed — leaving the field None — doesn't silently get explained as if accepted). Input: README + key files + the selector's own reason (used only as context for what angle to emphasize, explicitly not as source material to quote or restate — the explanation must be grounded in README/files, never in the selector's reasoning text). File tree is deliberately excluded from this stage's input — it doesn't help write "what it is / how it works / use cases." Output: a single field (explanation: str) via a pydantic model kept for architectural consistency even though structured output isn't strictly necessary for one field — this was a deliberate choice, not an oversight, to leave room for adding fields later without restructuring the stage. Prompt targets about 900 characters (roughly 100?140 words), explicitly forbids generic filler/hype language, explicitly instructs honest brevity over invented detail when evidence (key files, README) is thin, and again treats all fetched content as untrusted, not instructions.

5.8 Translator (nodes/translator.py)

Runs only for finalists with an English explanation. Input is only candidate.explanation_en — no README, no metadata, nothing else. No _format_candidate helper exists or is needed here (unlike every other stage), since there's exactly one string going in. Output: one field (translated: str) via a pydantic model, same "kept for consistency" reasoning as the explainer. System prompt (get_translator_prompt(), no parameters — it's fixed, since the only variable content is the human message) enforces a detailed Georgian style guide: natural modern Georgian sentence construction (not word-for-word calque from English), avoidance of stock bureaucratic phrases, preservation of brand/product/code/API-identifier/number text exactly as given, keeping established English technical terms untranslated when no precise Georgian equivalent exists, minimal/structure-only use of lists and headers, and a silent two-pass self-edit (grammar, then naturalness) before returning only the final clean text with no preamble or meta-commentary. Register is explicitly "technical expert explaining to a technically literate peer," not marketing copy and not casual chat.

5.9 Telegram Dispatch (nodes/telegram_dispatch.py) — async def dispatch_batch(candidates)

Runs on candidates with a non-empty explanation_ka. For each: generates a short random ID (secrets.token_hex(4)), saves {full_name, url, description, selector_reason} into the pending-repos store (via PendingRepos.add, keyed by that ID) before sending — this is the mechanism that lets the later, separate bot.py process know what a button tap refers to, since Telegram's callback_data is capped at 64 bytes and can't carry the full data. Builds message text (repo name, star count, URL, Georgian explanation) and an inline keyboard with two buttons: callback_data="a:<id>" (Accept) and "r:<id>" (Reject) — the colon separator is deliberate, for unambiguous parsing on the receiving end. Sends via aiogram's bot.send_message. Plain text, no parse_mode="Markdown" — an earlier version used Markdown and broke on repo names/descriptions containing _, *, `, which are common and would either corrupt formatting or fail the send outright. Calls PendingRepos.prune_old_entries() once at the start of the batch (not per-candidate) to drop stale unanswered entries (default 30-day cutoff). Wraps the whole send loop in try/finally to guarantee bot.session.close() runs even if something fails mid-batch.

5.10 On-tap handler (bot.py, not under nodes/ — it's not a pipeline stage, it's a separate always-on process)

An aiogram.Dispatcher with one callback-query handler filtered to callback_data starting with "a:" or "r:". On any tap:

Parse action + id from callback_data.
PendingRepos.get(id) — if None, the tap is for an already-handled or expired entry; answer "Already handled." and stop (this is the double-tap guard).
Accept: call star_repo(full_name); log the decision to PreferenceMemory.add_decision(...) with decision="accept" regardless of whether starring succeeded (the user's real choice is what matters for history, independent of a transient GitHub API failure); only if starring actually succeeded, also call RepoStatus.add_entry(...) to register it for future cleanup review — a repo that failed to star should never enter the cleanup-tracking file.
Reject: log to PreferenceMemory with decision="reject". No star, no RepoStatus entry.
Remove the entry from the pending store either way.
Edit the original Telegram message to replace the buttons with a plain result line ("✅ Starred: ..." / "⚠️ Accepted, but starring failed: ..." / "❌ Rejected: ...") — this is both the user-facing confirmation and a second layer of double-tap prevention (no buttons left to press).
Answer the callback query (required by Telegram's API or the button shows a permanent loading spinner).

Instantiates a fresh PendingRepos() / PreferenceMemory() / RepoStatus() per handled tap rather than holding one instance at module scope — this matters specifically because this process runs continuously for weeks, and a long-lived instance would hold a stale in-memory copy of a JSON file another process (cron-run cleanup, or a concurrent tap) has since changed underneath it.

5.11 Cleanup Agent (nodes/cleanup.py) — weekly, run_cleanup()

Completely separate flow from the discovery pipeline — reads only from repo_status.json, never touches Candidate/discovery state. For every tracked repo: skip immediately if functional is already True or False (a decision, once made, is final — never re-checked; see rationale below). Otherwise fetch current pushed_at via get_repo_info; skip (leave functional: null) if less than 21 days of silence. Otherwise fetch README/tree/key files (same github_client.py functions the main fetcher uses) and call the LLM with CleanupDecision (functional: bool, reason: str). Critical calibration, enforced in the prompt (get_cleanup_prompt): this stage answers a different question than the selector — not "is this a good idea" (already decided at discovery time) but "does this show concrete signs of a complete/stable state vs. an abandoned mid-build." Silence alone is explicitly insufficient evidence — a small, complete, finished utility can be untouched for months and still be fully functional. Only silence combined with concrete unfinished-state evidence (stub functions, TODOs on core logic, an entry point importing files absent from the tree, README describing features with no corresponding code) justifies functional: false. The prompt explicitly biases toward functional: true when evidence is thin or ambiguous, on the reasoning that a false positive (dead repo stays starred a while longer) is cheap, while a false negative (a genuinely good, complete repo gets permanently unstarred) is expensive and irreversible from the pipeline's point of view.

Why "never re-check once decided" is safe: this was a deliberate design discussion, not an oversight. The risk of never re-checking a functional: true verdict is that a repo could theoretically go stale after being marked functional and just sit starred forever unused — a low-cost, silent failure. The alternative (keep re-checking forever) risks eventually misjudging a genuinely fine repo as dead through some future evaluation error — an expensive, destructive, and irreversible failure. For a personal curation tool, trading away the cheap failure mode to eliminate the expensive one is the right tradeoff.

On functional: true: update repo_status.json entry (functional=true, last_checked=now), stop. On functional: false: call unstar_repo(full_name), and only if the unstar call actually succeeded, mark the entry functional=false — if unstar_repo returns False (API failure), the entry must be left at functional=null so the next weekly run retries it; marking it false on a failed unstar would silently leave the repo starred forever while the tracking file claims it was handled. (Verify this ordering is correctly implemented — it was flagged as a bug to fix and should be double-checked.)

6. Persistent state files (all in state.py, all plain JSON, all gitignored)
File	Class	Written by	Purpose
state.json	PollerState	poller.py, trending_poller.py	per-cluster last-checked timestamps + global cross-source "seen" set (_seen_repos)
history.json	PreferenceMemory	only bot.py, on real taps	append-only log of every real accept/reject decision — the taste signal for prompt_engineer.py
repo_status.json	RepoStatus	bot.py (on accept), cleanup.py (on review)	tracks starred repos: starred_at, selector_reason (carried over as context, not re-derived), functional, last_checked
pending.json	PendingRepos	telegram_dispatch.py (add), bot.py (get/remove)	short-id → repo-data mapping bridging the gap between a dispatched message and its eventual button tap, since callback_data can't carry full repo info

Known risk, worth re-verifying across all four classes: file paths must be resolved relative to the file's own location (Path(__file__).resolve().parent, i.e. a shared BASE_DIR), not the process's current working directory — cron and systemd typically start processes from different working directories, so a relative path default would cause bot.py and the cron-run pipeline to silently read/write different physical files. PendingRepos was fixed to use BASE_DIR; confirm PollerState, PreferenceMemory, and RepoStatus all do too.

Known risk: RepoStatus and PreferenceMemory are both written by more than one process (bot.py continuously, cleanup.py or the pipeline periodically via cron). Each class currently loads its JSON file into memory once per instance and rewrites the whole thing on every save. If any caller holds a long-lived instance across time (rather than constructing a fresh one right before each read/write), it risks overwriting concurrent changes made by the other process. PendingRepos was designed to always re-read from disk inside each method rather than caching; the same pattern should be confirmed/applied for RepoStatus at minimum, since it's written by both bot.py and cleanup.py.

PendingRepos.save_atomic writes to a .tmp file (fsync'd) then os.replaces it over the real file — this pattern (write-tmp-then-atomic-rename) should ideally be the standard for all four classes to avoid leaving corrupted JSON behind on a crash mid-write, though only PendingRepos currently does this.

7. Orchestration — pipeline.py and main.py

pipeline.py defines one function, async def run_pipeline(candidates: list[Candidate]) -> None, which is a flat, non-branching sequence — deliberately plain Python, not a LangGraph StateGraph, since the pipeline has no conditional branches or loops (that decision was made explicitly: a real graph library would add state-schema/node/edge machinery for no benefit on a straight-line pipeline). It takes an already-produced candidate list as input (not calling run_poller() itself internally) so that both the normal poller and the trending poller can feed the same pipeline:

python
async def run_pipeline(candidates: list[Candidate]) -> None:
    if not candidates:
        logger.info("[pipeline] no new candidates, stopping")
        return
    meta_prompt = generate_meta_prompt()
    candidates = validate_batch(candidates, meta_prompt)
    candidates = fetch_batch(candidates)
    candidates = select_batch(candidates, meta_prompt)
    candidates = explain_batch(candidates)
    candidates = translate_batch(candidates)
    await dispatch_batch(candidates)

main.py is the actual process entrypoint cron invokes, using argparse to select which scheduled job to run (discover, trending-week, trending-month, cleanup) since all four are separate cron schedules pointing at the same file:

0 */6 * * *  python main.py discover
0 9 * * 1    python main.py trending-week
0 9 1 * *    python main.py trending-month
0 3 * * 1    python main.py cleanup

bot.py is not invoked by cron or main.py at all — it must run as its own continuously-running process (planned as a systemd service on the VM) since Telegram button taps can arrive at any moment, not on a schedule.
