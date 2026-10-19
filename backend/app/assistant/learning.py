"""Typed-question support for learners: exact arithmetic with steps, honest about what it cannot do.

Nothing here guesses. A question is either (a) arithmetic, a percentage, a one-variable linear
equation or a check of a stated answer, which is computed exactly and then re-computed a second,
independent way; (b) answered by quoting the tenant's own learning material (done by the caller
with `retrieval`); or (c) met with a clarifying question. The visitor's raw text is never echoed
back or stored: only the normalized arithmetic is shown.
"""

import ast
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, DivisionByZero, InvalidOperation, localcontext
from fractions import Fraction
from typing import Any

AGE_BANDS = ("6-8", "9-11", "12-14", "15-17", "18+")
MINOR_BANDS = AGE_BANDS[:4]  # under 18: guardian confirmation is required
MAX_QUESTION_CHARS = 300
MAX_NODES = 25
# Level limits keep the tool age-appropriate: the youngest band gets whole-number arithmetic only.
BAND_MAX_VALUE = {"6-8": 10_000, "9-11": 10**9}
UNLIMITED = 10**12

LANGS = ("en", "hi", "hinglish")
_HINGLISH_MARKERS = frozenset(
    "kya hai hain kitna kitni kitne batao bataiye ka ke ki mein se aur jodo ghatao guna bhag "
    "hota hoga nikalo karo sahi galat".split()
)

TEXT: dict[str, dict[str, str]] = {
    "en": {
        "answer": "Answer",
        "step": "Step",
        "check": "Check",
        "correct": "Yes, that is correct.",
        "incorrect": "That is not correct.",
        "note": "I can make mistakes. Please check important answers with your teacher or a book.",
        "clarify": "I am not sure what you mean. Could you write the exact question, for example "
        "'12 x 7' or '3x + 2 = 11'?",
        "not_found": "I could not find this in the learning material, so I will not guess. "
        "Please ask your teacher.",
        "ambiguous": "This can be read two ways. Which one do you mean?",
        "too_hard": "This is beyond the level set for your age group. Please ask your teacher.",
        "zero": "Dividing by zero is not possible.",
        "too_big": "These numbers are too big for me to work with safely.",
        "bodmas": "Rule: brackets first, then powers, then × and ÷, then + and − (left to right).",
        "percent": "Percent means 'out of 100'.",
        "move": "Move the plain numbers to the other side",
        "divide": "Divide both sides by",
        "no_solution": "There is no value of the unknown that makes this true.",
        "any_value": "Every value of the unknown makes this true.",
        "remainder": "remainder",
        "guardian": "A parent or guardian must confirm before a learner under 18 uses this.",
        "consent": "A consent link from your school or parent is needed to use this. "
        "Please ask your teacher or parent.",
        "unconfirmed": "I could not double-check this result, so please treat it as unverified.",
    },
    "hi": {
        "answer": "उत्तर",
        "step": "चरण",
        "check": "जाँच",
        "correct": "हाँ, यह सही है।",
        "incorrect": "यह सही नहीं है।",
        "note": "मुझसे गलती हो सकती है। ज़रूरी उत्तर अपने शिक्षक या किताब से जाँच लें।",
        "clarify": "मैं समझ नहीं पाया। कृपया सवाल ठीक से लिखें, जैसे '12 x 7' या '3x + 2 = 11'।",
        "not_found": "मुझे यह सीखने की सामग्री में नहीं मिला, इसलिए मैं अंदाज़ा नहीं लगाऊँगा। कृपया शिक्षक से पूछें।",
        "ambiguous": "इसके दो मतलब हो सकते हैं। आपका कौन-सा है?",
        "too_hard": "यह आपकी आयु-श्रेणी के स्तर से आगे है। कृपया शिक्षक से पूछें।",
        "zero": "शून्य से भाग नहीं दिया जा सकता।",
        "too_big": "ये संख्याएँ मेरे लिए सुरक्षित रूप से हल करने के लिए बहुत बड़ी हैं।",
        "bodmas": "नियम: पहले कोष्ठक, फिर घात, फिर × और ÷, फिर + और − (बाएँ से दाएँ)।",
        "percent": "प्रतिशत का मतलब है 'सौ में से'।",
        "move": "सादी संख्याओं को दूसरी ओर ले जाएँ",
        "divide": "दोनों ओर भाग दें",
        "no_solution": "अज्ञात का कोई ऐसा मान नहीं है जिससे यह सही हो।",
        "any_value": "अज्ञात का हर मान इसे सही बनाता है।",
        "remainder": "शेषफल",
        "guardian": "18 वर्ष से कम के शिक्षार्थी के उपयोग से पहले माता-पिता या अभिभावक की पुष्टि चाहिए।",
        "consent": "इसका उपयोग करने के लिए आपके स्कूल या माता-पिता से सहमति लिंक चाहिए। "
        "कृपया अपने शिक्षक या माता-पिता से पूछें।",
        "unconfirmed": "मैं इस परिणाम की दोबारा जाँच नहीं कर सका, इसलिए इसे अपुष्ट मानें।",
    },
    "hinglish": {
        "answer": "Answer",
        "step": "Step",
        "check": "Check",
        "correct": "Haan, yeh sahi hai.",
        "incorrect": "Yeh sahi nahi hai.",
        "note": "Mujhse galti ho sakti hai. Zaroori answers apne teacher ya book se check kar lo.",
        "clarify": "Main samajh nahi paya. Kripya sawal exact likho, jaise '12 x 7' ya "
        "'3x + 2 = 11'.",
        "not_found": "Yeh mujhe learning material mein nahi mila, isliye main andaza nahi "
        "lagaunga. Teacher se pooch lo.",
        "ambiguous": "Isko do tarah se padha ja sakta hai. Aapka matlab kaun sa hai?",
        "too_hard": "Yeh aapki age group ke level se aage hai. Teacher se pooch lo.",
        "zero": "Zero se divide nahi kiya ja sakta.",
        "too_big": "Yeh numbers mere liye safely solve karne ke liye bahut bade hain.",
        "bodmas": "Rule: pehle brackets, phir power, phir × aur ÷, phir + aur − (left se right).",
        "percent": "Percent ka matlab 'sau mein se'.",
        "move": "Seedhe numbers ko dusri side le jao",
        "divide": "Dono side divide karo",
        "no_solution": "Unknown ki aisi koi value nahi jo isko sahi banaye.",
        "any_value": "Unknown ki har value isko sahi banati hai.",
        "remainder": "remainder",
        "guardian": "18 saal se kam ke learner ke use se pehle parent ya guardian ki "
        "confirmation chahiye.",
        "consent": "Isse use karne ke liye aapke school ya parent se consent link chahiye. "
        "Please apne teacher ya parent se poochhein.",
        "unconfirmed": "Main is result ko dobara check nahi kar paya, isliye ise unverified maano.",
    },
}


def detect_language(text: str) -> str:
    letters = [c for c in text if c.isalpha()]
    if letters and sum("ऀ" <= c <= "ॿ" for c in letters) / len(letters) > 0.3:
        return "hi"
    words = set(re.findall(r"[a-z]+", text.lower()))
    return "hinglish" if len(words & _HINGLISH_MARKERS) >= 1 else "en"


# ----------------------------------------------------------------------------- normalization

_WORD_OPS: list[tuple[str, str]] = [
    (r"multiplied\s+by|\btimes\b|\bguna\b|\bgunaa\b|गुणा", " * "),
    (r"divided\s+by|\bbhag\b|\bbhaag\b|भाग", " / "),
    (r"\bplus\b|\bjodo\b|\bjod\b|जोड़ो|जोड़", " + "),
    (r"\bminus\b|\bghatao\b|\bghata\b|घटाओ|घटा", " - "),
]
_FILLER = re.compile(
    r"\b(what|is|are|the|calculate|solve|find|value|of|answer|please|tell|me|kya|hai|hota|hoga|"
    r"kitna|kitne|batao|nikalo|sahi|galat|correct|right|true|equals?|equal|to)\b|क्या|है|कितना|"
    r"बताओ|सही|गलत|[?!,]",
    re.IGNORECASE,
)
_PERCENT = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:%|percent|pratishat|प्रतिशत)\s*(?:of|ka|ki|ke|का|की|के)?\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)


def _ascii_digits(text: str) -> str:
    return "".join(str(unicodedata.digit(c)) if c.isdigit() else c for c in text)


def normalize(question: str) -> str:
    s = unicodedata.normalize("NFKC", question).lower()
    s = _ascii_digits(s).replace("−", "-").replace("–", "-").replace("÷", "/").replace("×", "*")
    s = s.replace("^", "**").replace("：", ":")
    s = re.sub(r"(?<=\d)\s*x\s*(?=[\d(])", " * ", s)  # 12 x 7, 3x(2) -> multiplication
    for pattern, symbol in _WORD_OPS:
        s = re.sub(pattern, symbol, s, flags=re.IGNORECASE)
    return s


# ----------------------------------------------------------------------------- exact evaluation


class MathRefused(Exception):  # noqa: N818 - domain term
    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


def fmt(x: Fraction) -> str:
    if x.denominator == 1:
        return str(x.numerator)
    d = x.denominator
    for p in (2, 5):
        while d % p == 0:
            d //= p
    if d == 1:
        return f"{float(x):.10f}".rstrip("0").rstrip(".")
    return f"{x.numerator}/{x.denominator} (≈ {float(x):.4f})"


_SYMBOL = {ast.Add: "+", ast.Sub: "−", ast.Mult: "×", ast.Div: "÷", ast.Pow: "^"}


def _check_limit(value: Fraction, limit: int) -> None:
    if abs(value.numerator) > limit * 10**6 or value.denominator > 10**12:
        raise MathRefused("too_big")


def _eval(node: ast.AST, steps: list[str], limit: int, count: list[int]) -> Fraction:
    count[0] += 1
    if count[0] > MAX_NODES:
        raise MathRefused("clarify")
    if isinstance(node, ast.Expression):
        return _eval(node.body, steps, limit, count)
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
        if isinstance(node.value, bool):
            raise MathRefused("clarify")
        value = Fraction(str(node.value))
        _check_limit(value, limit)
        return value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
        inner = _eval(node.operand, steps, limit, count)
        return -inner if isinstance(node.op, ast.USub) else inner
    if isinstance(node, ast.BinOp) and type(node.op) in _SYMBOL:
        left = _eval(node.left, steps, limit, count)
        right = _eval(node.right, steps, limit, count)
        op = type(node.op)
        if op is ast.Add:
            result = left + right
        elif op is ast.Sub:
            result = left - right
        elif op is ast.Mult:
            result = left * right
        elif op is ast.Div:
            if right == 0:
                raise MathRefused("zero")
            result = left / right
        else:
            if right.denominator != 1 or not 0 <= right <= 6 or abs(left) > 10**6:
                raise MathRefused("too_big")
            result = left ** int(right)
        _check_limit(result, limit)
        line = f"{fmt(left)} {_SYMBOL[op]} {fmt(right)} = {fmt(result)}"
        if (
            op is ast.Div
            and left.denominator == right.denominator == 1
            and result.denominator != 1
            and abs(left) > abs(right)
        ):
            q, r = divmod(left.numerator, right.numerator)
            line += f"  ({q} r {r})"
        steps.append(line)
        return result
    raise MathRefused("clarify")


def _decimal_eval(node: ast.AST) -> Decimal:
    """Second, independent computation (Decimal arithmetic) used only to cross-check the first."""
    if isinstance(node, ast.Expression):
        return _decimal_eval(node.body)
    if isinstance(node, ast.Constant):
        return Decimal(str(node.value))
    if isinstance(node, ast.UnaryOp):
        v = _decimal_eval(node.operand)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp):
        a, b = _decimal_eval(node.left), _decimal_eval(node.right)
        if isinstance(node.op, ast.Add):
            return a + b
        if isinstance(node.op, ast.Sub):
            return a - b
        if isinstance(node.op, ast.Mult):
            return a * b
        if isinstance(node.op, ast.Div):
            return a / b
        return a ** int(b)
    raise InvalidOperation


def verified(tree: ast.Expression, result: Fraction) -> bool:
    try:
        with localcontext() as ctx:
            ctx.prec = 60
            other = _decimal_eval(tree)
            exact = Decimal(result.numerator) / Decimal(result.denominator)
            return abs(other - exact) <= Decimal("1e-30") * max(Decimal(1), abs(exact))
    except (InvalidOperation, DivisionByZero, ValueError):
        return False


def _parse_expr(text: str) -> ast.Expression:
    try:
        tree = ast.parse(text.strip(), mode="eval")
    except SyntaxError as exc:
        raise MathRefused("clarify") from exc
    return tree


# ----------------------------------------------------------------------------- linear equations


def _lin(node: ast.AST, var: str) -> tuple[Fraction, Fraction]:
    """(a, b) for a*var + b."""
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
        return Fraction(0), Fraction(str(node.value))
    if isinstance(node, ast.Name) and node.id == var:
        return Fraction(1), Fraction(0)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        a, b = _lin(node.operand, var)
        return -a, -b
    if isinstance(node, ast.BinOp):
        (a1, b1), (a2, b2) = _lin(node.left, var), _lin(node.right, var)
        if isinstance(node.op, ast.Add):
            return a1 + a2, b1 + b2
        if isinstance(node.op, ast.Sub):
            return a1 - a2, b1 - b2
        if isinstance(node.op, ast.Mult):
            if a1 == 0:
                return b1 * a2, b1 * b2
            if a2 == 0:
                return a1 * b2, b1 * b2
        if isinstance(node.op, ast.Div) and a2 == 0 and b2 != 0:
            return a1 / b2, b1 / b2
    raise MathRefused("clarify")


# ----------------------------------------------------------------------------- the solver


@dataclass
class LearningResult:
    kind: str  # math | clarify | refused | not_math
    language: str
    steps: list[str] = field(default_factory=list)
    result: str | None = None
    verdict: str | None = None  # correct | incorrect, when the learner stated an answer
    message: str = ""
    options: list[str] = field(default_factory=list)
    confidence: str = "high"
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "language": self.language,
            "steps": self.steps,
            "result": self.result,
            "verdict": self.verdict,
            "message": self.message,
            "options": self.options,
            "confidence": self.confidence,
            "notes": self.notes,
            "data_stored": False,
        }


def _limit_for(band: str) -> int:
    return BAND_MAX_VALUE.get(band, UNLIMITED)


_VAR = re.compile(r"(?<![a-z])([a-z])(?![a-z])")
_AMBIGUOUS_IMPLICIT = re.compile(r"[/]\s*\d+(?:\.\d+)?\s*\(")


_TOKEN = re.compile(r"\d+(?:\.\d+)?|\*\*|[-+*/()]")


def _segments(text: str) -> list[str]:
    """Maximal arithmetic runs in `text`. Two numbers with no operator between them start a new
    run, so '3 apples and 2 oranges, 6 x 7' can never be glued into '3 2 6 x 7'."""
    runs: list[list[str]] = []
    current: list[str] = []
    expect_operand = True
    for piece in _TOKEN.findall(text):
        is_number = piece[0].isdigit()
        if expect_operand:
            if is_number or piece == "(":
                current.append(piece)
                expect_operand = piece == "("
            elif piece == "-" and (not current or current[-1] in "(+-*/" or current[-1] == "**"):
                current.append(piece)
            else:
                runs.append(current)
                current, expect_operand = [], True
                if is_number or piece == "(":
                    current.append(piece)
                    expect_operand = piece == "("
        elif piece in ("+", "-", "*", "/", "**"):
            current.append(piece)
            expect_operand = True
        elif piece == ")":
            current.append(piece)
        elif piece == "(":
            current.append(piece)  # implicit multiplication, checked for ambiguity later
            expect_operand = True
        else:  # a number right after a number
            runs.append(current)
            current, expect_operand = [piece], False
    runs.append(current)
    return [
        "".join(r) for r in runs if any(t in r for t in ("+", "-", "*", "/", "**")) and len(r) > 2
    ]


_WORD = re.compile(r"[^\W\d_]{2,}")


def solve(question: str, *, band: str, language: str = "auto") -> LearningResult:
    """Tries arithmetic / percent / linear / answer-check. `kind='not_math'` means 'not for me'."""
    lang = detect_language(question) if language == "auto" else language
    t = TEXT[lang]
    raw = question.strip()[:MAX_QUESTION_CHARS]
    s = normalize(raw)
    limit = _limit_for(band)
    out = LearningResult("math", lang, notes=[t["note"]])

    try:
        m = _PERCENT.search(s)
        if m:
            return _percent(m, out, t, limit)

        s = _FILLER.sub(" ", re.sub(r"\[[^\]]*\]", " ", s))
        claimed: Fraction | None = None
        if "=" in s:
            if s.count("=") != 1:
                raise MathRefused("clarify")
            left, right = (_WORD.sub(" ", part).strip() for part in s.split("="))
            left, right = (re.sub(r"[^\w\s.+\-*/()?]", " ", p).strip() for p in (left, right))
            if _VAR.search(left) or _VAR.search(right):
                return _linear(left, right, out, t, band)
            left = re.sub(r"[^\d\s.+\-*/()]", " ", left)
            digits = re.sub(r"[^\d.\-]", "", right.replace(",", ""))
            if digits:
                try:
                    claimed = Fraction(digits)
                except (ValueError, ZeroDivisionError):
                    raise MathRefused("clarify") from None
            segments = _segments(left)
        else:
            segments = _segments(re.sub(r"[^\d\s.+\-*/()]", " ", s))

        if not segments:
            if re.search(r"\d\s*[-+*/]|[-+*/]\s*\d", s):
                raise MathRefused("clarify")  # it looks like arithmetic but cannot be read
            out.kind = "not_math"
            out.notes = []
            return out
        if len(segments) > 1:
            raise MathRefused("clarify")
        expr = segments[0]

        if _AMBIGUOUS_IMPLICIT.search(expr):
            return _ambiguous(expr, out, t, limit)
        expr = re.sub(r"(?<=\d)\(", "*(", expr)
        if expr.count("(") != expr.count(")"):
            raise MathRefused("clarify")

        tree = _parse_expr(expr)
        steps: list[str] = []
        result = _eval(tree, steps, limit, [0])
        if band == "6-8" and (result.denominator != 1 or result < 0):
            raise MathRefused("too_hard")
        return _finish(out, t, tree, result, steps, claimed)
    except MathRefused as refused:
        if refused.key == "clarify":
            out.kind = "clarify"
            out.message = t["clarify"]
        else:
            out.kind = "clarify" if refused.key == "too_hard" else "refused"
            out.message = t[refused.key]
            out.confidence = "n/a"
        out.notes = [t["note"]]
        out.steps = []
        return out


def _finish(
    out: LearningResult,
    t: dict[str, str],
    tree: ast.Expression,
    result: Fraction,
    steps: list[str],
    claimed: Fraction | None,
) -> LearningResult:
    if len(steps) > 1:
        out.steps.append(t["bodmas"])
    out.steps += [f"{t['step']} {i}: {line}" for i, line in enumerate(steps, 1)]
    out.result = fmt(result)
    if not verified(tree, result):
        out.confidence = "low"
        out.notes.insert(0, t["unconfirmed"])
    if claimed is not None:
        out.verdict = "correct" if claimed == result else "incorrect"
        out.message = t["correct"] if out.verdict == "correct" else t["incorrect"]
    out.message = (out.message + " " if out.message else "") + f"{t['answer']}: {out.result}"
    return out


def _percent(
    m: re.Match[str], out: LearningResult, t: dict[str, str], limit: int
) -> LearningResult:
    p, n = Fraction(m.group(1)), Fraction(m.group(2))
    _check_limit(p, limit)
    _check_limit(n, limit)
    result = n * p / 100
    out.steps = [
        t["percent"],
        f"{t['step']} 1: {fmt(p)} ÷ 100 = {fmt(p / 100)}",
        f"{t['step']} 2: {fmt(n)} × {fmt(p / 100)} = {fmt(result)}",
    ]
    out.result = fmt(result)
    out.message = f"{t['answer']}: {out.result}"
    return out


def _ambiguous(expr: str, out: LearningResult, t: dict[str, str], limit: int) -> LearningResult:
    """a / b(c) can mean (a/b)*c or a/(b*c); do not pick one for the learner."""
    match = re.search(r"(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)\(", expr)
    if not match:
        raise MathRefused("clarify")
    readings = []
    for candidate in (
        expr.replace(match.group(0), f"({match.group(1)}/{match.group(2)})*("),
        expr.replace(match.group(0), f"{match.group(1)}/({match.group(2)}*("),
    ):
        if candidate.count("(") > candidate.count(")"):
            candidate += ")" * (candidate.count("(") - candidate.count(")"))
        try:
            readings.append(f"{candidate} = {fmt(_eval(_parse_expr(candidate), [], limit, [0]))}")
        except MathRefused:
            raise MathRefused("clarify") from None
    out.kind = "clarify"
    out.message = t["ambiguous"]
    out.options = readings
    out.confidence = "n/a"
    return out


def _linear(
    left: str, right: str, out: LearningResult, t: dict[str, str], band: str
) -> LearningResult:
    if band in ("6-8", "9-11"):
        raise MathRefused("too_hard")
    names = set(_VAR.findall(left)) | set(_VAR.findall(right))
    if len(names) != 1:
        raise MathRefused("clarify")
    var = names.pop()

    def prep(text: str) -> ast.Expression:
        text = re.sub(rf"(\d)\s*({var})\b", r"\1*\2", text.replace(" ", ""))
        return _parse_expr(text)

    if not left or not right:
        raise MathRefused("clarify")
    a1, b1 = _lin(prep(left).body, var)
    a2, b2 = _lin(prep(right).body, var)
    a, b = a1 - a2, b2 - b1
    out.steps = [f"{t['step']} 1: {left.strip()} = {right.strip()}"]
    if a == 0:
        out.message = t["any_value"] if b == 0 else t["no_solution"]
        out.confidence = "high"
        return out
    coefficient = "" if a == 1 else fmt(a)
    out.steps.append(f"{t['step']} 2: {t['move']}: {coefficient}{var} = {fmt(b)}")
    value = b / a
    if a != 1:
        out.steps.append(f"{t['step']} 3: {t['divide']} {fmt(a)}: {var} = {fmt(value)}")
    lhs = a1 * value + b1
    rhs = a2 * value + b2
    ok = lhs == rhs
    out.steps.append(f"{t['check']}: {fmt(lhs)} = {fmt(rhs)} {'✓' if ok else '✗'}")
    out.result = f"{var} = {fmt(value)}"
    out.message = f"{t['answer']}: {out.result}"
    if not ok:
        out.confidence = "low"
        out.notes.insert(0, t["unconfirmed"])
    return out


def guardian_message(language: str) -> str:
    return TEXT[language if language in TEXT else "en"]["guardian"]


def consent_message(language: str) -> str:
    return TEXT[language if language in TEXT else "en"]["consent"]


def text_for(language: str, key: str) -> str:
    return TEXT[language if language in TEXT else "en"][key]
