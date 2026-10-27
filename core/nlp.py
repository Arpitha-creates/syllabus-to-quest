"""The NLP engine: text in, topics / keyphrases / quiz items out.

Deliberately dependency-free (standard library only). No model downloads, no
network calls at runtime:

* keyphrase extraction  -> a RAKE-style co-occurrence scorer
* summarisation         -> extractive sentence scoring over those keyphrases
* quiz generation       -> cloze (fill-the-blank) + topic-recall items
* syllabus parsing      -> unit / module / chapter heading detection

Everything is deterministic: identical input always yields identical quests,
which is what makes the generated quizzes testable.
"""

from __future__ import annotations

import hashlib
import math
import random
import re
from collections import Counter
from typing import Iterable

# --------------------------------------------------------------------------- #
# vocabulary
# --------------------------------------------------------------------------- #
STOPWORDS: set[str] = set("""
a an the and or but if then than that this these those of in on at to for from by with
without into over under about above below as is are was were be been being it its we you
they he she i not no do does did done can could should would may might must will shall
have has had also such other others more most some any all each every both few many much
own same so too very just now use used using etc via per eg ie vs within between during
after before while when where which who whom whose what how why there their them then
one two three four five first second third e g i t s re ll ve d m o
introduction overview basics concept concepts basic understand understanding describe
explain study learn learning course syllabus unit units chapter module topic topics
students student shall will able following given including include includes
""".split())

# expository verbs: a phrase containing one of these makes a clumsy blank
CLAUSE_VERBS: set[str] = set("""
gives give counts count follows follow keeps keep restores restore builds build maps map
solves solve uses use avoids avoid detects detect allows allow makes make moves move
stores store costs cost runs run needs need requires require holds hold divides divide
sorts sort merges merge explores explore visits visit computes compute answers answer
maintains maintain writes write loads load evicts evict handles handle shares share
limits limit prevents prevent occurs occur reduces reduce raises raise turns turn
spends spend drives drive orders order schedules schedule tracks track queues queue
cannot succeed averages average compares compare enables enable provides provide
contains contain becomes become remains remain applies apply depends depend
""".split())

HEADING_RE = re.compile(
    r"^\s*(?:unit|module|chapter|week|lesson|part|section|topic|paper)\b\s*"
    r"[-–:.#]?\s*([0-9]+|[ivxlcdm]+)?\s*[-–:.)]?\s*(.*)$",
    re.I,
)
NUMBERED_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)\s*[.)\-:]?\s+(.+)$")
BULLET_RE = re.compile(r"^\s*[-–•*\u2022\u25cf]\s+(.+)$")
# administrative sections that are not study material
BOILERPLATE_RE = re.compile(
    r"^\s*(recommended|reference|text ?books?|further reading|assessment|evaluation|"
    r"examination|grading|prerequisites?|learning outcomes?|course outcomes?|objectives?|"
    r"credits?|attendance|policy|marks)\b",
    re.I,
)
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9'’\-]*")


# --------------------------------------------------------------------------- #
# basic text utilities
# --------------------------------------------------------------------------- #
def clean_text(text: str) -> str:
    """Normalise whitespace and strip common PDF artefacts."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\u00ad\u200b\ufeff]", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def tokenize(text: str) -> list[str]:
    return WORD_RE.findall(text)


def split_sentences(text: str) -> list[str]:
    """Lightweight sentence splitter that survives abbreviations and bullets."""
    flat = re.sub(r"\s+", " ", text).strip()
    if not flat:
        return []
    flat = re.sub(r"\b(e\.g|i\.e|etc|vs|fig|no)\.", r"\1<dot>", flat, flags=re.I)
    parts = re.split(r"(?<=[.!?;])\s+(?=[A-Z0-9(\"'])", flat)
    out: list[str] = []
    for part in parts:
        part = part.replace("<dot>", ".").strip(" •–-")
        if len(part.split()) >= 4:
            out.append(part)
    return out


def _content_tokens(text: str) -> list[str]:
    return [t for t in tokenize(text) if len(t) > 2 and t.lower() not in STOPWORDS]


def stable_seed(*parts: object) -> int:
    """Deterministic seed so a given prompt always yields the same quiz."""
    raw = "|".join(str(p) for p in parts)
    return int(hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8], 16)


# --------------------------------------------------------------------------- #
# keyphrase extraction (RAKE)
# --------------------------------------------------------------------------- #
def _chunks(tokens: list[str], max_words: int = 4) -> list[list[str]]:
    chunks, current = [], []
    for token in tokens:
        if token.lower() in STOPWORDS or len(token) < 3 or token.isdigit():
            if current:
                chunks.append(current)
                current = []
        else:
            current.append(token)
            if len(current) >= max_words:
                chunks.append(current)
                current = []
    if current:
        chunks.append(current)
    return chunks


def keyphrase_scores(text: str, max_words: int = 3) -> dict[str, float]:
    """Score every candidate phrase with RAKE: degree(w) / freq(w), summed."""
    tokens = tokenize(text)
    chunks = _chunks(tokens, max_words=max_words)
    freq: Counter[str] = Counter()
    degree: Counter[str] = Counter()
    candidates: list[list[str]] = []

    for chunk in chunks:
        lowered = [w.lower() for w in chunk]
        for n in range(1, min(max_words, len(chunk)) + 1):
            for i in range(len(chunk) - n + 1):
                phrase_words = lowered[i:i + n]
                candidates.append(phrase_words)
                for w in phrase_words:
                    freq[w] += 1
                    degree[w] += len(phrase_words)

    word_score = {w: degree[w] / freq[w] for w in freq if freq[w]}
    scores: dict[str, float] = {}
    display: dict[str, str] = {}
    for words in candidates:
        key = " ".join(words)
        scores[key] = scores.get(key, 0.0) + sum(word_score.get(w, 0.0) for w in words)
        if key not in display:
            display[key] = " ".join(words)
    return scores


def keyphrases(text: str, top_k: int = 12, max_words: int = 3) -> list[str]:
    """Return the top scoring phrases, sub-phrases of better phrases removed."""
    scores = keyphrase_scores(text, max_words=max_words)
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    kept: list[str] = []
    for phrase, _score in ranked:
        if any(phrase != other and phrase in other for other in kept):
            continue
        if len(kept) >= top_k:
            break
        kept.append(phrase)
    return [p[0].upper() + p[1:] for p in kept]


# --------------------------------------------------------------------------- #
# summarisation
# --------------------------------------------------------------------------- #
def summarize(text: str, max_sentences: int = 2) -> str:
    """Extractive summary: highest keyphrase density, returned in reading order."""
    sentences = split_sentences(text)
    if not sentences:
        return ""
    scores = keyphrase_scores(text)
    word_score: Counter[str] = Counter()
    for phrase, score in scores.items():
        for word in phrase.split():
            word_score[word] = max(word_score[word], score)

    ranked: list[tuple[float, int]] = []
    for idx, sentence in enumerate(sentences):
        content = _content_tokens(sentence)
        if not content:
            continue
        value = sum(word_score.get(w.lower(), 0.0) for w in content) / math.sqrt(len(content))
        ranked.append((value, idx))
    ranked.sort(key=lambda pair: (-pair[0], pair[1]))
    chosen = sorted(idx for _v, idx in ranked[:max_sentences])
    return " ".join(sentences[i] for i in chosen)


# --------------------------------------------------------------------------- #
# syllabus structure parsing
# --------------------------------------------------------------------------- #
def _clean_title(raw: str) -> str:
    title = re.sub(r"\s+", " ", raw).strip(" .-–:;,·•")
    title = re.sub(r"\s*\(.*?\)\s*$", "", title).strip()
    if len(title) > 90:
        title = title[:87].rsplit(" ", 1)[0] + "..."
    return title or "Untitled unit"


def _looks_like_heading(line: str) -> tuple[bool, str]:
    stripped = line.strip()
    if not stripped or len(stripped) > 120:
        return False, ""
    match = HEADING_RE.match(stripped)
    if match and (match.group(1) or match.group(2)):
        body = match.group(2).strip(" -–:.)")
        if body:
            return True, body
    numbered = NUMBERED_RE.match(stripped)
    if numbered and "." not in numbered.group(1) and len(numbered.group(2).split()) <= 12:
        return True, numbered.group(2)
    if stripped.isupper() and 2 <= len(stripped.split()) <= 10:
        return True, stripped.title()
    return False, ""


def extract_topics(text: str, min_topics: int = 2) -> list[dict]:
    """Split a syllabus into topics with title, body, summary and weight 1-5."""
    text = clean_text(text)
    lines = text.split("\n")
    topics: list[dict] = []
    current: dict | None = None
    preamble: list[str] = []

    skipping = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        is_heading, heading_text = _looks_like_heading(stripped)
        if is_heading:
            skipping = False
            current = {"title": _clean_title(heading_text), "body_lines": []}
            topics.append(current)
            continue
        if BOILERPLATE_RE.match(stripped):
            # drop admin sections (textbooks, marks, policies) from the quest line
            skipping = True
            current = None
            continue
        if skipping:
            continue
        bullet = BULLET_RE.match(stripped)
        numbered = NUMBERED_RE.match(stripped)
        content = stripped
        if bullet:
            content = bullet.group(1).strip()
        elif numbered:
            content = numbered.group(2).strip()
        if current is None:
            preamble.append(content)
        else:
            current["body_lines"].append(content)

    if len(topics) < min_topics:
        # no usable headings - fall back to paragraph-level units
        prose = _topics_from_prose(text)
        cleaned = []
        for item in prose:
            body = item["body"].strip()
            cleaned.append({
                "title": item["title"],
                "body": body,
                "summary": summarize(body, max_sentences=2),
            })
        return _assign_weights(cleaned)

    # Course headers (course code, credits, semester) are metadata, not study
    # material - they stay out of the quest line.

    cleaned: list[dict] = []
    for item in topics:
        body = "\n".join(item["body_lines"]).strip()
        if not body:
            body = item["title"]
        cleaned.append({
            "title": item["title"],
            "body": body,
            "summary": summarize(body, max_sentences=2),
        })

    return _assign_weights(cleaned)


def _topics_from_prose(text: str) -> list[dict]:
    """Fallback for notes without headings: chunk by paragraph, then by sentences."""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", clean_text(text)) if b.strip()]
    if len(blocks) < 2:
        sentences = split_sentences(text)
        blocks = [
            " ".join(sentences[i:i + 4]) for i in range(0, len(sentences), 4)
        ]
    topics = []
    for block in blocks:
        phrases = keyphrases(block, top_k=1)
        title = phrases[0] if phrases else _clean_title(block[:60])
        topics.append({"title": _clean_title(title), "body": block})
    return topics[:40] or [{"title": "Whole document", "body": text}]


def _assign_weights(topics: list[dict]) -> list[dict]:
    """Importance 1-5 by body length rank (longer unit = heavier quest)."""
    if not topics:
        return topics
    lengths = sorted(len(t["body"].split()) for t in topics)
    for topic in topics:
        words = len(topic["body"].split())
        rank = sum(1 for n in lengths if n <= words) / len(lengths)
        topic["weight"] = max(1, min(5, 1 + round(rank * 4)))
    return topics


# --------------------------------------------------------------------------- #
# question generation
# --------------------------------------------------------------------------- #
def _blank(sentence: str, phrase: str) -> str | None:
    pattern = re.compile(re.escape(phrase), re.I)
    if not pattern.search(sentence):
        return None
    return pattern.sub("______", sentence, count=1)


def _cloze_value(phrase: str, doc_freq: dict[str, int]) -> float | None:
    """How good a blank this phrase makes. None means 'not suitable'.

    A good answer is a distinctive noun-ish phrase: it carries no everyday
    verb, and its words are rare in the rest of the document (so the blank
    tests this unit rather than generic vocabulary).
    """
    words = phrase.lower().split()
    if any(w in CLAUSE_VERBS for w in words):
        return None
    rarity = sum(1.0 / (1.0 + doc_freq.get(w, 0)) for w in words)
    return rarity + 0.12 * len(words)


def cloze_questions(
    title: str,
    body: str,
    distractor_pool: Iterable[str] | None = None,
    limit: int = 4,
    doc_freq: dict[str, int] | None = None,
) -> list[dict]:
    """Fill-the-blank items built from the topic's own sentences."""
    pool = [p for p in (distractor_pool or []) if len(p.split()) >= 1]
    phrases = keyphrases(body, top_k=25)
    sentences = split_sentences(body)
    if not sentences or not phrases:
        return []
    if doc_freq is None:
        doc_freq = dict(Counter(w.lower() for w in tokenize(body)))

    rng = random.Random(stable_seed(title, body[:200]))
    # rank sentences by how many keyphrases they carry
    scored: list[tuple[int, int, str]] = []
    for idx, sentence in enumerate(sentences):
        hits = sum(1 for p in phrases if p.lower() in sentence.lower())
        if hits:
            scored.append((hits, idx, sentence))
    scored.sort(key=lambda item: (-item[0], item[1]))

    questions: list[dict] = []
    used_phrases: set[str] = set()
    for _hits, _idx, sentence in scored:
        if len(questions) >= limit:
            break
        scored_candidates = []
        for p in phrases:
            if p.lower() in used_phrases or not (1 <= len(p.split()) <= 4):
                continue
            if p.lower() not in sentence.lower():
                continue
            if len(sentence.split()) - len(p.split()) < 3:
                continue
            value = _cloze_value(p, doc_freq)
            if value is None:
                continue
            scored_candidates.append((value, p))
        if not scored_candidates:
            continue
        answer = max(scored_candidates, key=lambda pair: (pair[0], len(pair[1])))[1]
        prompt = _blank(sentence, answer)
        if not prompt:
            continue
        options = _build_options(answer, pool, rng)
        if options is None:
            continue
        used_phrases.add(answer.lower())
        questions.append({
            "qtype": "cloze",
            "prompt": f"[{title}] Fill in the blank: {prompt}",
            "options": options,
            "answer": answer,
            "explanation": sentence,
        })
    return questions


def _build_options(answer: str, pool: list[str], rng: random.Random) -> list[str] | None:
    """Correct answer plus three plausible, unique distractors."""
    answer_key = answer.lower().strip()
    target_words = len(answer.split())
    seen = {answer_key}
    candidates: list[str] = []
    shuffled = list(pool)
    rng.shuffle(shuffled)
    for phrase in shuffled:
        key = phrase.lower().strip()
        if key in seen or len(key) < 3:
            continue
        # prefer distractors of a similar length to the answer
        if abs(len(phrase.split()) - target_words) > 2:
            continue
        seen.add(key)
        candidates.append(phrase)
        if len(candidates) == 3:
            break
    if len(candidates) < 3:
        return None
    options = candidates + [answer]
    rng.shuffle(options)
    return options


def recall_questions(
    topic: dict,
    other_topics: list[dict],
    limit: int = 2,
) -> list[dict]:
    """Two cross-topic item types: 'which statement fits this unit' and the reverse."""
    questions: list[dict] = []
    if not other_topics:
        return questions
    rng = random.Random(stable_seed(topic["title"], "recall"))

    best = summarize(topic["body"], max_sentences=1)
    if best and len(best.split()) >= 6:
        options = [best]
        for other in other_topics[:6]:
            alt = summarize(other["body"], max_sentences=1)
            if alt and alt != best and alt not in options:
                options.append(alt)
        if len(options) >= 3:
            options = options[:4]
            rng.shuffle(options)
            questions.append({
                "qtype": "recall",
                "prompt": f"Which statement belongs to the unit '{topic['title']}'?",
                "options": options,
                "answer": best,
                "explanation": f"'{topic['title']}' covers: {best}",
            })

    if len(other_topics) >= 3:
        statement = summarize(topic["body"], max_sentences=1)
        titles = [topic["title"]] + [t["title"] for t in other_topics[:3]]
        unique_titles = list(dict.fromkeys(titles))
        if statement and len(unique_titles) >= 4:
            options = list(unique_titles[:4])
            rng.shuffle(options)
            questions.append({
                "qtype": "mapping",
                "prompt": f"Which unit does this come from? \"{statement}\"",
                "options": options,
                "answer": topic["title"],
                "explanation": f"This belongs to '{topic['title']}'.",
            })
    return questions[:limit]


def true_false_questions(topic: dict, other_topics: list[dict], limit: int = 1) -> list[dict]:
    """A real statement (True) or one transplanted from another unit (False)."""
    questions: list[dict] = []
    statement = summarize(topic["body"], max_sentences=1)
    if not statement:
        return questions
    rng = random.Random(stable_seed(topic["title"], "tf"))
    make_false = bool(rng.getrandbits(1)) and bool(other_topics)
    if make_false:
        donor = rng.choice(other_topics)
        shown = summarize(donor["body"], max_sentences=1) or donor["title"]
        answer = "False"
        explanation = f"That statement belongs to '{donor['title']}', not '{topic['title']}'."
    else:
        shown = statement
        answer = "True"
        explanation = f"True - this is stated in '{topic['title']}'."
    questions.append({
        "qtype": "truefalse",
        "prompt": f"[{topic['title']}] True or false: {shown}",
        "options": ["True", "False"],
        "answer": answer,
        "explanation": explanation,
    })
    return questions[:limit]


def build_topic_questions(topic: dict, other_topics: list[dict], pool: list[str],
                          n_cloze: int = 4, n_recall: int = 2,
                          doc_freq: dict[str, int] | None = None) -> list[dict]:
    """All quiz items for one unit: cloze first, then recall, then true/false."""
    items = cloze_questions(topic["title"], topic["body"], pool, limit=n_cloze,
                            doc_freq=doc_freq)
    items += recall_questions(topic, other_topics, limit=n_recall)
    items += true_false_questions(topic, other_topics, limit=1)
    return items


def document_pool(topics: list[dict]) -> list[str]:
    """Distractor phrase pool drawn from every unit in the document."""
    pool: list[str] = []
    for topic in topics:
        pool.extend(keyphrases(topic["body"], top_k=12))
        pool.append(topic["title"])
    return list(dict.fromkeys(p for p in pool if 2 <= len(p) <= 60))
