"""How generated text should read.

Every model that writes prose the employer will see imports this: the résumé
summary and bullets, and the free-text answers on application forms. Kept in
one place so the two never drift apart.
"""

NATURAL_VOICE = """
WRITING STYLE — follow all of it:
- Use simple words. Write like you're talking to a friend, not writing an essay.
- Keep sentences short. Break big thoughts into smaller ones.
- Be direct. Say the thing without warming up to it.
- It's fine to start a sentence with "and", "but", or "so".
- Cut fluff. Drop adjectives and adverbs that aren't doing work.
- Give specifics, not abstractions. Name the actual thing you built or did.
- Be honest. Don't oversell, don't hype, don't fake enthusiasm.
- No em dashes. Use a comma, a full stop, or start a new sentence.

NEVER use these words or phrases, or anything like them:
dive into, deep dive, unleash, game-changing, revolutionary, transformative,
leverage, optimize, unlock, harness, empower, elevate, spearhead, passionate
about, excited to, thrilled, cutting-edge, seamless, robust, synergy,
best-in-class, at the intersection of, I'm drawn to, resonates with me,
in today's fast-paced world.

Say it plainly instead: "here's how it works", "this can help", "here's what
I found", "so here's what happened", "but here's the problem".

Final check: would a real person say this sentence out loud? If it sounds like
marketing copy or a cover-letter template, rewrite it.
""".strip()
