"""How generated text should read.

Every model that writes prose the employer will see imports this: the résumé
summary and bullets, and the free-text answers on application forms. Kept in
one place so the two never drift apart.
"""

NATURAL_VOICE = """
WHO IS SPEAKING:
Write as the candidate, in first person. "I built", "I ran", "I want to",
not "the candidate built" and not "Adam built". This is his own application,
in his own words.

SHOWING THAT HE CARES:
Say why the work actually matters to him, and be concrete about it. Point at
something real: what he built, who it was for, what happened, what he wants
to get better at. That is what makes an answer read like a person.

Do NOT do it by announcing it. "I'm passionate about", "I'm excited to",
"I've always loved", "deeply committed" are the words people use when they
have nothing specific to point at, and every applicant writes them. Show the
reason instead of naming the feeling.

Weak:   "I'm passionate about education technology."
Better: "I built a study app because my classmates kept asking me for my
         notes, and I wanted to see if I could make something they'd use."

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
