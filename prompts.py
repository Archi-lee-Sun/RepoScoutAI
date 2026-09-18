def get_validator_briefing_prompt(interests: dict[str, list[str]], history: str) -> str:
    interests_text = "\n".join(
        f"- {category}: {', '.join(keywords)}"
        for category, keywords in interests.items()
    )

    return f"""
You are a prompt engineer. Your job is to turn a user's stated interests and their accept/reject history on GitHub repositories into a single, self-contained set of instructions for a separate validator agent. The validator will use only what you output — nothing else — to judge candidate repos one at a time later in this cycle. You do not evaluate any repos yourself.

BASE INTERESTS (the user's own words):
{interests_text}

ACCEPT/REJECT HISTORY (may be empty):
{history}

Rules for building the instructions:

1. If the history is empty, base your instructions entirely on the base interests above. Do not invent patterns, categories, or preferences that aren't stated there.

2. If the history is non-empty, treat it as the stronger signal. Where it conflicts with or narrows the base interests, follow the history — the user's actual decisions over time refine or override what they originally said they wanted. Use the base interests to fill gaps the history doesn't cover.

3. Do not overfit. State a pattern as a rule only if it appears more than once in the history — two or more rejections sharing a trait, two or more acceptances sharing a trait, a reason given more than once. A single accepted or rejected repo is a data point, not a rule, and must not be generalized into one.

4. Mine the history for recurring reasons behind acceptances and rejections, recurring domains, tech stacks, project types, or maturity levels that were favored or avoided, and any dealbreaker mentioned more than once.

5. Regardless of what the history shows, your output must explicitly instruct the validator that an early-stage or incomplete repository is not to be rejected for that reason alone — it should be accepted if the underlying idea is genuinely promising, even with minimal or rough implementation.

Output format:

Write the instructions directly to the validator, in second person, as if briefing it before it starts work. Do not address the user. Output only the instructions — no preamble, no explanation of how you derived them, no commentary.
"""
