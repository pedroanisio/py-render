"""The scene-render expression language (schema/scene-render-1.1.xsd, expressionType).

A pure, side-effect-free subset of ECMAScript expressions. Source text is split by
a hand-written tokenizer and parsed by a Pratt parser into a tree of closures;
user text never reaches Python's ``eval``/``exec``.

A program is zero or more ``var|let|const name = expr`` declarations (or bare
``name = expr`` assignments) followed by a final expression; statements are
separated by ``;`` or newlines. Its value is that of the last expression or
assignment statement.

Values are floats, strings, bools, ``None`` (null), :data:`UNDEFINED`, lists
(After Effects vectors: ``+``/``-`` are elementwise, ``*``/``/`` by a number
scale), dicts (read-only objects whose keys are members) and Python callables
supplied through the environment. Tuples, ints and numpy values coming from the
environment or from callables are normalised to these.

Typical use::

    env = {**builtin_functions(seed, time, value=value),
           "time": time, "frame": frame, "value": value, "seed": seed,
           "param": param, "prop": prop, ...}
    result = compile_expr(src)(env)
"""
from __future__ import annotations

import math
import re
import struct
from contextvars import ContextVar
from functools import lru_cache
from typing import Callable, Mapping, NamedTuple, Sequence

import numpy as np

__all__ = ["ExprError", "Expr", "UNDEFINED", "compile_expr", "builtin_functions", "make_wiggle",
           "to_num", "to_str", "truthy"]

MAX_SOURCE_BYTES = 65536
MAX_STEPS = 1_000_000
MAX_DEPTH = 200
MAX_OCTAVES = 16
_MAX_STR_ITEMS = 1_000_000


class ExprError(Exception):
    """Raised for syntax errors, forbidden access and runtime errors in an expression."""


class _Undefined:
    __slots__ = ()

    def __repr__(self) -> str:
        return "undefined"

    def __bool__(self) -> bool:
        return False


UNDEFINED = _Undefined()
_FORBIDDEN = frozenset({"constructor", "prototype", "__proto__", "caller", "callee", "arguments"})


def _check_name(name: str) -> None:
    if name.startswith("__") or name in _FORBIDDEN:
        raise ExprError(f"access to {name!r} is not allowed")


# ---------------------------------------------------------------- value semantics

def _py(v: object) -> object:
    """Normalise a host value into the expression value domain."""
    if v is None or v is UNDEFINED or isinstance(v, (bool, str, float, list, dict)):
        return v
    if isinstance(v, int):
        try:
            return float(v)
        except OverflowError:
            return math.inf if v > 0 else -math.inf
    if isinstance(v, tuple):
        return [_py(x) for x in v]
    if isinstance(v, np.ndarray):
        return _py(v.tolist())
    if isinstance(v, np.generic):
        return _py(v.item())
    return v


def _kind(v: object) -> str:
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    if v is None:
        return "null"
    if v is UNDEFINED:
        return "undefined"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return "function" if callable(v) else "other"


def _typeof(v: object) -> str:
    k = _kind(v)
    return k if k in ("boolean", "number", "string", "undefined", "function") else "object"


def truthy(v: object) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v == v and v != 0
    if isinstance(v, str):
        return bool(v)
    return v is not None and v is not UNDEFINED


_DEC = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_DEC_PREFIX = re.compile(r"[+-]?(?:Infinity|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)")
_RADIX = {"x": 16, "b": 2, "o": 8}


def _str_to_num(s: str) -> float:
    s = s.strip()
    if not s:
        return 0.0
    if _DEC.fullmatch(s):
        return float(s)
    if len(s) > 2 and s[0] == "0" and s[1].lower() in _RADIX:
        try:
            return float(int(s[2:], _RADIX[s[1].lower()]))
        except ValueError:
            return math.nan
    return {"Infinity": math.inf, "+Infinity": math.inf, "-Infinity": -math.inf}.get(s, math.nan)


def to_num(v: object) -> float:
    """ECMAScript ToNumber (a one-element list converts like its element)."""
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(_py(v))  # type: ignore[arg-type]
    if v is None:
        return 0.0
    if isinstance(v, str):
        return _str_to_num(v)
    if isinstance(v, list):
        return 0.0 if not v else to_num(v[0]) if len(v) == 1 else math.nan
    if isinstance(v, np.generic):
        return to_num(v.item())
    return math.nan


def _num_str(x: float) -> str:
    if x != x:
        return "NaN"
    if math.isinf(x):
        return "Infinity" if x > 0 else "-Infinity"
    if x == int(x) and abs(x) < 1e21:
        return str(int(x))
    return re.sub(r"e([+-])0*(\d)", r"e\1\2", repr(x))


def to_str(v: object, _budget: list[int] | None = None) -> str:
    """ECMAScript ToString (lists join with ``,``)."""
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return _num_str(float(v))
    if v is None:
        return "null"
    if v is UNDEFINED:
        return "undefined"
    if isinstance(v, (list, tuple)):
        budget = _budget if _budget is not None else [_MAX_STR_ITEMS]
        budget[0] -= len(v)
        if budget[0] < 0:
            raise ExprError("array too large to convert to a string")
        return ",".join("" if x is None or x is UNDEFINED else to_str(x, budget) for x in v)
    if isinstance(v, dict):
        return "[object Object]"
    return "function" if callable(v) else to_str(_py(v))


def _to_int32(v: object) -> int:
    x = to_num(v)
    if not math.isfinite(x):
        return 0
    n = int(x) & 0xFFFFFFFF
    return n - (1 << 32) if n & 0x80000000 else n


def _strict_eq(x: object, y: object) -> bool:
    k = _kind(x)
    if k != _kind(y):
        return False
    return x == y if k in ("number", "string", "boolean", "null", "undefined") else x is y


def _loose_eq(x: object, y: object) -> bool:
    kx, ky = _kind(x), _kind(y)
    if kx == ky:
        return _strict_eq(x, y)
    if {kx, ky} <= {"null", "undefined"}:
        return True
    if kx == "boolean":
        return _loose_eq(to_num(x), y)
    if ky == "boolean":
        return _loose_eq(x, to_num(y))
    if {kx, ky} == {"number", "string"}:
        return to_num(x) == to_num(y)
    if kx == "array" and ky in ("number", "string"):
        return _loose_eq(to_str(x), y)
    if ky == "array" and kx in ("number", "string"):
        return _loose_eq(x, to_str(y))
    return False


def _div(a: float, b: float) -> float:
    if b == 0:
        return math.nan if a == 0 or a != a else math.copysign(math.inf, a) * math.copysign(1.0, b)
    return a / b


def _mod(a: float, b: float) -> float:
    if b == 0 or not math.isfinite(a) or b != b:
        return math.nan
    return a if math.isinf(b) else math.fmod(a, b)


def _pow(a: float, b: float) -> float:
    if b != b:
        return math.nan
    if b == 0:
        return 1.0
    if abs(a) == 1 and math.isinf(b):
        return math.nan
    try:
        return math.pow(a, b)
    except OverflowError:
        return -math.inf if a < 0 and b == int(b) and int(b) % 2 else math.inf
    except ValueError:
        return math.inf if a == 0 else math.nan


def _vmap(f: Callable[..., float], *xs: object) -> object:
    """Apply scalar ``f`` componentwise when any argument is a list (scalars broadcast, short lists pad with 0)."""
    if not any(isinstance(x, list) for x in xs):
        return f(*map(to_num, xs))
    n = max(len(x) for x in xs if isinstance(x, list))  # type: ignore[arg-type]
    comp = lambda x, i: (to_num(x[i]) if i < len(x) else 0.0) if isinstance(x, list) else to_num(x)
    return [f(*(comp(x, i) for x in xs)) for i in range(n)]


class _Ctx:
    __slots__ = ("env", "locals", "steps", "max_steps", "draws")

    def __init__(self, env: Mapping[str, object], max_steps: int) -> None:
        self.env, self.locals, self.steps, self.max_steps, self.draws = env, {}, 0, max_steps, 0

    def tick(self, n: int = 1) -> None:
        self.steps += n
        if self.steps > self.max_steps:
            raise ExprError(f"expression exceeded {self.max_steps} evaluation steps")

    def lookup(self, name: str) -> object:
        if name in self.locals:
            return self.locals[name]
        try:
            return _py(self.env[name])
        except KeyError:
            pass
        if name in _GLOBALS:
            return _GLOBALS[name]
        raise ExprError(f"unknown identifier {name!r}")


_CURRENT: ContextVar[_Ctx | None] = ContextVar("scenerender_expr_ctx", default=None)


def _vector_op(ctx: _Ctx, op: str, x: object, y: object) -> object:
    xl, yl = isinstance(x, list), isinstance(y, list)
    if op in "+-":
        if xl and yl:
            ctx.tick(max(len(x), len(y)))  # type: ignore[arg-type]
            return _vmap((lambda a, b: a + b) if op == "+" else (lambda a, b: a - b), x, y)
        if op == "+" and (isinstance(x, str) or isinstance(y, str)):
            s = to_str(x) + to_str(y)
            ctx.tick(1 + len(s) // 8)
            return s
        if xl or yl:
            raise ExprError(f"cannot apply {op!r} to {_kind(x)} and {_kind(y)}")
        return to_num(x) + to_num(y) if op == "+" else to_num(x) - to_num(y)
    if xl and yl or yl and op == "/":
        raise ExprError(f"cannot apply {op!r} to {_kind(x)} and {_kind(y)}")
    if xl or yl:
        vec, k = (x, y) if xl else (y, x)
        ctx.tick(len(vec))  # type: ignore[arg-type]
        k = to_num(k)
        return [to_num(c) * k if op == "*" else _div(to_num(c), k) for c in vec]  # type: ignore[union-attr]
    return to_num(x) * to_num(y) if op == "*" else _div(to_num(x), to_num(y))


def _lt(x: object, y: object) -> bool:
    return x < y if isinstance(x, str) and isinstance(y, str) else to_num(x) < to_num(y)


def _le(x: object, y: object) -> bool:
    return x <= y if isinstance(x, str) and isinstance(y, str) else to_num(x) <= to_num(y)


_BINOPS: dict[str, Callable[[_Ctx, object, object], object]] = {
    "+": lambda c, x, y: _vector_op(c, "+", x, y),
    "-": lambda c, x, y: _vector_op(c, "-", x, y),
    "*": lambda c, x, y: _vector_op(c, "*", x, y),
    "/": lambda c, x, y: _vector_op(c, "/", x, y),
    "%": lambda c, x, y: _mod(to_num(x), to_num(y)),
    "**": lambda c, x, y: _pow(to_num(x), to_num(y)),
    "<": lambda c, x, y: _lt(x, y),
    ">": lambda c, x, y: _lt(y, x),
    "<=": lambda c, x, y: _le(x, y),
    ">=": lambda c, x, y: _le(y, x),
    "==": lambda c, x, y: _loose_eq(x, y),
    "!=": lambda c, x, y: not _loose_eq(x, y),
    "===": lambda c, x, y: _strict_eq(x, y),
    "!==": lambda c, x, y: not _strict_eq(x, y),
    "&": lambda c, x, y: float(_to_int32(x) & _to_int32(y)),
    "|": lambda c, x, y: float(_to_int32(x) | _to_int32(y)),
    "^": lambda c, x, y: float(_to_int32(x) ^ _to_int32(y)),
    "<<": lambda c, x, y: float(_to_int32(_to_int32(x) << (_to_int32(y) & 31))),
    ">>": lambda c, x, y: float(_to_int32(x) >> (_to_int32(y) & 31)),
    ">>>": lambda c, x, y: float((_to_int32(x) & 0xFFFFFFFF) >> (_to_int32(y) & 31)),
}


def _neg(ctx: _Ctx, v: object) -> object:
    if isinstance(v, list):
        ctx.tick(len(v))
        return [-to_num(c) for c in v]
    return -to_num(v)


_UNOPS: dict[str, Callable[[_Ctx, object], object]] = {
    "-": _neg,
    "+": lambda c, v: to_num(v),
    "!": lambda c, v: not truthy(v),
    "~": lambda c, v: float(~_to_int32(v)),
    "typeof": lambda c, v: _typeof(v),
}


def _member(obj: object, key: str) -> object:
    _check_name(key)
    if isinstance(obj, dict):
        if key in obj:
            return _py(obj[key])
        raise ExprError(f"unknown member {key!r}")
    if key == "length" and isinstance(obj, (str, list)):
        return float(len(obj))
    raise ExprError(f"cannot read property {key!r} of {_kind(obj)}")


def _index(obj: object, key: object) -> object:
    if isinstance(obj, (list, str)) and isinstance(key, (int, float)) and not isinstance(key, bool):
        if 0 <= key < len(obj) and key == int(key):
            return _py(obj[int(key)])
        return UNDEFINED
    return _member(obj, to_str(key))


# ---------------------------------------------------------------- globals

def _mathfn(f: Callable[..., float]) -> Callable[..., float]:
    def g(*args: object) -> float:
        try:
            return float(f(*map(to_num, args)))
        except OverflowError:
            return math.inf
        except ValueError:
            return math.nan
    return g


def _finite_or(f: Callable[[float], float]) -> Callable[[float], float]:
    return lambda x: x if not math.isfinite(x) else float(f(x))


def _log(f: Callable[[float], float]) -> Callable[[float], float]:
    return lambda x: -math.inf if x == 0 else f(x)


def _minmax(pick: Callable[..., float], empty: float) -> Callable[..., float]:
    return lambda *xs: math.nan if any(x != x for x in xs) else pick(xs) if xs else empty


_MATH: dict[str, object] = {
    **{k: _mathfn(f) for k, f in {
        "abs": abs, "sin": math.sin, "cos": math.cos, "tan": math.tan, "asin": math.asin,
        "acos": math.acos, "atan": math.atan, "atan2": math.atan2, "sqrt": math.sqrt,
        "cbrt": lambda x: math.copysign(abs(x) ** (1 / 3), x), "pow": _pow, "exp": math.exp,
        "log": _log(math.log), "log2": _log(math.log2), "log10": _log(math.log10),
        "floor": _finite_or(math.floor), "ceil": _finite_or(math.ceil), "trunc": _finite_or(math.trunc),
        "round": _finite_or(lambda x: math.floor(x + 0.5)),
        "sign": lambda x: x if x != x else float((x > 0) - (x < 0)),
        "min": _minmax(min, math.inf), "max": _minmax(max, -math.inf), "hypot": math.hypot,
    }.items()},
    "PI": math.pi, "E": math.e, "LN2": math.log(2), "LN10": math.log(10),
    "LOG2E": 1 / math.log(2), "LOG10E": 1 / math.log(10), "SQRT2": math.sqrt(2), "SQRT1_2": math.sqrt(0.5),
}


def _parse_float(s: object = UNDEFINED) -> float:
    m = _DEC_PREFIX.match(to_str(s).strip())
    return float(m.group().replace("Infinity", "inf")) if m else math.nan


def _parse_int(s: object = UNDEFINED, radix: object = UNDEFINED) -> float:
    text = to_str(s).strip()
    sign = -1.0 if text.startswith("-") else 1.0
    if text.startswith(("+", "-")):
        text = text[1:]
    r = _to_int32(radix) if radix is not UNDEFINED else 0
    if r in (0, 16) and text[:2].lower() == "0x":
        text, r = text[2:], 16
    r = r or 10
    if not 2 <= r <= 36:
        return math.nan
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"[:r]
    n = 0
    while n < len(text) and text[n].lower() in digits:
        n += 1
    return sign * float(int(text[:n], r)) if n else math.nan


_GLOBALS: dict[str, object] = {
    "Math": _MATH,
    "Number": lambda v=0.0: to_num(v),
    "String": lambda v="": to_str(v),
    "parseFloat": _parse_float,
    "parseInt": _parse_int,
    "isNaN": lambda v=UNDEFINED: math.isnan(to_num(v)),
    "isFinite": lambda v=UNDEFINED: math.isfinite(to_num(v)),
    "NaN": math.nan,
    "Infinity": math.inf,
}


# ---------------------------------------------------------------- tokenizer

class _Tok(NamedTuple):
    kind: str  # num | str | id | op | eof
    val: object
    pos: int
    nl: bool  # a line break precedes this token


_TOKEN = re.compile(r"""
    (?P<ws>[ \t\r\n\f\v\u00a0\u2028\u2029\ufeff]+|//[^\n]*|/\*.*?\*/)
  | (?P<num>0[xX][0-9a-fA-F]+|0[bB][01]+|0[oO][0-7]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)
  | (?P<str>"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')
  | (?P<id>[A-Za-z_$][A-Za-z0-9_$]*)
  | (?P<op>===|!==|>>>|\*\*|<=|>=|==|!=|&&|\|\||\?\?|<<|>>|[-+*/%<>!~&|^?:.,;()\[\]=])
""", re.X | re.S)
_ESC = re.compile(r"\\(u\{[0-9a-fA-F]+\}|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|\r\n|.)", re.S)
_SIMPLE_ESC = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0",
               "\n": "", "\r\n": "", "\u2028": "", "\u2029": ""}


def _unescape(body: str) -> str:
    def rep(m: re.Match[str]) -> str:
        e = m.group(1)
        if len(e) > 1 and e[0] in "ux":
            cp = int(e[2:-1] if e.startswith("u{") else e[1:], 16)
            if cp > 0x10FFFF:
                raise ExprError(f"invalid code point escape \\{e}")
            return chr(cp)
        return _SIMPLE_ESC.get(e, e)
    s = _ESC.sub(rep, body)
    try:
        return s.encode("utf-16", "surrogatepass").decode("utf-16")
    except UnicodeDecodeError:
        return s


def _tokenize(src: str) -> list[_Tok]:
    toks: list[_Tok] = []
    pos, nl = 0, False
    while pos < len(src):
        m = _TOKEN.match(src, pos)
        if not m:
            raise ExprError(f"unexpected character {src[pos]!r} at offset {pos}")
        kind, text = m.lastgroup, m.group()
        if kind == "ws":
            nl = nl or any(c in text for c in "\n\r\u2028\u2029")
        elif kind == "num":
            radix = _RADIX.get(text[1:2].lower()) if len(text) > 1 and text[0] == "0" else None
            toks.append(_Tok("num", float(int(text[2:], radix)) if radix else float(text), pos, nl))
            nl = False
        elif kind == "str":
            toks.append(_Tok("str", _unescape(text[1:-1]), pos, nl))
            nl = False
        else:
            toks.append(_Tok(kind, text, pos, nl))  # type: ignore[arg-type]
            nl = False
        pos = m.end()
    toks.append(_Tok("eof", None, len(src), True))
    return toks


# ---------------------------------------------------------------- parser

class _Node(NamedTuple):
    fn: Callable[[_Ctx], object]
    depth: int
    name: str | None = None  # dotted name for identifiers/members, used by typeof and error messages


_LBP = {"?": 2, "??": 3, "||": 3, "&&": 4, "|": 5, "^": 6, "&": 7,
        "==": 8, "!=": 8, "===": 8, "!==": 8, "<": 9, "<=": 9, ">": 9, ">=": 9,
        "<<": 10, ">>": 10, ">>>": 10, "+": 11, "-": 11, "*": 12, "/": 12, "%": 12, "**": 13,
        ".": 16, "[": 16, "(": 16}
_UNARY_BP = 14
_KEYWORDS = {"true": True, "false": False, "null": None, "undefined": UNDEFINED}
_DECL = {"var", "let", "const"}
_RESERVED = frozenset({*_KEYWORDS, *_DECL, "typeof", "function", "new", "delete", "void", "this",
                       "return", "if", "else", "for", "while", "do", "in", "of", "instanceof", "class"})

Stmt = tuple[str, str | None, _Node]  # ("decl" | "assign" | "expr", target, value)


class _Parser:
    def __init__(self, src: str) -> None:
        self.toks = _tokenize(src)
        self.i = 0
        self.level = 0

    def peek(self) -> _Tok:
        return self.toks[self.i]

    def next(self) -> _Tok:
        t = self.toks[self.i]
        self.i += 1
        return t

    def is_op(self, val: str, t: _Tok | None = None) -> bool:
        t = t or self.peek()
        return t.kind == "op" and t.val == val

    def expect(self, val: str) -> _Tok:
        t = self.next()
        if not self.is_op(val, t):
            raise self.error(t, f"expected {val!r}")
        return t

    def error(self, t: _Tok, msg: str | None = None) -> ExprError:
        what = "end of input" if t.kind == "eof" else repr(t.val)
        return ExprError(f"{msg or 'unexpected token'} ({what}) at offset {t.pos}")

    def node(self, fn: Callable[[_Ctx], object], *kids: _Node, name: str | None = None) -> _Node:
        depth = 1 + max((k.depth for k in kids), default=0)
        if depth > MAX_DEPTH:
            raise ExprError(f"expression nested deeper than {MAX_DEPTH}")
        return _Node(fn, depth, name)

    def ident(self) -> str:
        t = self.next()
        if t.kind != "id" or t.val in _RESERVED:
            raise self.error(t, "expected identifier")
        _check_name(t.val)  # type: ignore[arg-type]
        return t.val  # type: ignore[return-value]

    # statements
    def program(self) -> list[Stmt]:
        stmts: list[Stmt] = []
        while self.peek().kind != "eof":
            if self.is_op(";"):
                self.next()
                continue
            t, t2 = self.peek(), self.toks[self.i + 1]
            if t.kind == "id" and t.val in _DECL:
                self.next()
                while True:
                    name = self.ident()
                    value = self.const(UNDEFINED)
                    if self.is_op("="):
                        self.next()
                        value = self.expr()
                    stmts.append(("decl", name, value))
                    if not self.is_op(","):
                        break
                    self.next()
            elif t.kind == "id" and self.is_op("=", t2):
                name = self.ident()
                self.next()
                stmts.append(("assign", name, self.expr()))
            else:
                stmts.append(("expr", None, self.expr()))
            end = self.peek()
            if not (self.is_op(";", end) or end.nl):
                raise self.error(end)
        if not any(kind != "decl" for kind, _, _ in stmts):
            raise ExprError("expression has no result: it must end with an expression")
        return stmts

    # expressions
    def expr(self, rbp: int = 0) -> _Node:
        self.level += 1
        if self.level > MAX_DEPTH:
            raise ExprError(f"expression nested deeper than {MAX_DEPTH}")
        left = self.prefix(self.next())
        while (t := self.peek()).kind == "op" and _LBP.get(t.val, 0) > rbp:  # type: ignore[arg-type]
            left = self.infix(self.next(), left)
        self.level -= 1
        return left

    def const(self, v: object) -> _Node:
        return _Node(lambda ctx: v, 1)

    def prefix(self, t: _Tok) -> _Node:
        if t.kind in ("num", "str"):
            return self.const(t.val)
        if t.kind == "id":
            name: str = t.val  # type: ignore[assignment]
            if name in _KEYWORDS:
                return self.const(_KEYWORDS[name])
            if name == "typeof":
                return self.unary("typeof")
            if name in _RESERVED:
                raise self.error(t, "unsupported keyword")
            _check_name(name)

            def lookup(ctx: _Ctx) -> object:
                ctx.tick()
                return ctx.lookup(name)
            return _Node(lookup, 1, name)
        if t.kind == "op":
            if t.val == "(":
                inner = self.expr()
                self.expect(")")
                return inner
            if t.val == "[":
                items = self.items("]")
                fns = [n.fn for n in items]

                def array(ctx: _Ctx) -> object:
                    ctx.tick(1 + len(fns))
                    return [f(ctx) for f in fns]
                return self.node(array, *items)
            if t.val in ("-", "+", "!", "~"):
                return self.unary(t.val)  # type: ignore[arg-type]
        raise self.error(t)

    def unary(self, op: str) -> _Node:
        operand = self.expr(_UNARY_BP)
        if self.is_op("**"):
            raise self.error(self.peek(), "unary operator before '**' needs parentheses")
        f, x = _UNOPS[op], operand.fn
        if op == "typeof" and operand.name is not None and "." not in operand.name:
            name = operand.name

            def typeof_name(ctx: _Ctx) -> object:
                ctx.tick()
                try:
                    return _typeof(ctx.lookup(name))
                except ExprError:
                    return "undefined"
            return self.node(typeof_name, operand)

        def run(ctx: _Ctx) -> object:
            ctx.tick()
            return f(ctx, x(ctx))
        return self.node(run, operand)

    def items(self, close: str) -> list[_Node]:
        out: list[_Node] = []
        while not self.is_op(close):
            out.append(self.expr())
            if not self.is_op(","):
                break
            self.next()
        self.expect(close)
        return out

    def infix(self, t: _Tok, left: _Node) -> _Node:
        op: str = t.val  # type: ignore[assignment]
        a = left.fn
        if op == "?":
            then = self.expr()
            self.expect(":")
            other = self.expr(_LBP["?"] - 1)
            b, c = then.fn, other.fn
            return self.node(lambda ctx: b(ctx) if truthy(a(ctx)) else c(ctx), left, then, other)
        if op == ".":
            k = self.next()
            if k.kind != "id":
                raise self.error(k, "expected property name")
            key: str = k.val  # type: ignore[assignment]
            _check_name(key)

            def member(ctx: _Ctx) -> object:
                ctx.tick()
                return _member(a(ctx), key)
            return self.node(member, left, name=f"{left.name or '(expression)'}.{key}")
        if op == "[":
            index = self.expr()
            self.expect("]")
            b = index.fn

            def subscript(ctx: _Ctx) -> object:
                ctx.tick()
                return _index(a(ctx), b(ctx))
            return self.node(subscript, left, index)
        if op == "(":
            args = self.items(")")
            fns, desc = [n.fn for n in args], left.name or "expression"

            def call(ctx: _Ctx) -> object:
                ctx.tick()
                f = a(ctx)
                if not callable(f) or isinstance(f, type):
                    raise ExprError(f"{desc} is not a function")
                argv = [g(ctx) for g in fns]
                try:
                    return _py(f(*argv))
                except ExprError:
                    raise
                except (TypeError, ValueError, ArithmeticError, IndexError, KeyError) as e:
                    raise ExprError(f"error calling {desc}: {e}") from e
            return self.node(call, left, *args)
        right = self.expr(_LBP[op] - 1 if op == "**" else _LBP[op])
        b = right.fn
        if op in ("&&", "||", "??"):
            def logical(ctx: _Ctx) -> object:
                ctx.tick()
                v = a(ctx)
                if op == "&&":
                    return b(ctx) if truthy(v) else v
                if op == "||":
                    return v if truthy(v) else b(ctx)
                return b(ctx) if v is None or v is UNDEFINED else v
            return self.node(logical, left, right)
        f = _BINOPS[op]

        def binary(ctx: _Ctx) -> object:
            ctx.tick()
            return f(ctx, a(ctx), b(ctx))
        return self.node(binary, left, right)


class Expr:
    """A compiled expression program; call it with an environment mapping."""

    __slots__ = ("src", "_stmts")

    def __init__(self, src: str, stmts: Sequence[Stmt]) -> None:
        self.src, self._stmts = src, tuple(stmts)

    def __repr__(self) -> str:
        return f"Expr({self.src if len(self.src) < 60 else self.src[:57] + '...'!r})"

    def __call__(self, env: Mapping[str, object], *, max_steps: int = MAX_STEPS) -> object:
        ctx = _Ctx(env, max_steps)
        token = _CURRENT.set(ctx)
        try:
            result: object = UNDEFINED
            for kind, name, node in self._stmts:
                v = node.fn(ctx)
                if name is not None:
                    ctx.locals[name] = v
                if kind != "decl":
                    result = v
            return result
        except RecursionError as e:
            raise ExprError("expression recursion too deep") from e
        finally:
            _CURRENT.reset(token)


@lru_cache(maxsize=4096)
def compile_expr(src: str) -> Expr:
    """Parse ``src`` into an :class:`Expr` (cached by source text)."""
    if not isinstance(src, str):
        raise ExprError("expression source must be a string")
    if len(src) > MAX_SOURCE_BYTES // 4 and len(src.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise ExprError(f"expression longer than {MAX_SOURCE_BYTES} bytes")
    try:
        return Expr(src, _Parser(src).program())
    except RecursionError as e:
        raise ExprError("expression nested too deeply") from e


# ---------------------------------------------------------------- deterministic helpers

_M64 = (1 << 64) - 1


def _mix64(x: int) -> int:
    x = (x + 0x9E3779B97F4A7C15) & _M64
    x = ((x ^ (x >> 30)) * 0xBF58476D1CE4E5B9) & _M64
    x = ((x ^ (x >> 27)) * 0x94D049BB133111EB) & _M64
    return x ^ (x >> 31)


def _hash01(*words: int) -> float:
    h = 0
    for w in words:
        h = _mix64(h ^ (w & _M64))
    return (h >> 11) / float(1 << 53)


def _float_bits(x: float) -> int:
    return struct.unpack("<Q", struct.pack("<d", float(x) + 0.0))[0]


@lru_cache(maxsize=64)
def _perm(seed: int) -> tuple[int, ...]:
    p = list(range(256))
    for i in range(255, 0, -1):
        j = int(_hash01(seed, 0x5EED, i) * (i + 1))
        p[i], p[j] = p[j], p[i]
    return tuple(p + p)


def _grad(h: int, x: float, y: float, z: float) -> float:
    h &= 15
    u = x if h < 8 else y
    v = y if h < 4 else x if h in (12, 14) else z
    return (u if h & 1 == 0 else -u) + (v if h & 2 == 0 else -v)


def _fade(t: float) -> float:
    return t * t * t * (t * (t * 6 - 15) + 10)


def _perlin(p: Sequence[int], x: float, y: float, z: float) -> float:
    """Improved Perlin gradient noise, clamped to [-1, 1]."""
    if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
        return math.nan
    fx, fy, fz = math.floor(x), math.floor(y), math.floor(z)
    X, Y, Z = int(fx) & 255, int(fy) & 255, int(fz) & 255
    x, y, z = x - fx, y - fy, z - fz
    u, v, w = _fade(x), _fade(y), _fade(z)
    A, B = p[X] + Y, p[X + 1] + Y
    AA, AB, BA, BB = p[A] + Z, p[A + 1] + Z, p[B] + Z, p[B + 1] + Z
    lerp = lambda t, a, b: a + t * (b - a)
    r = lerp(w,
             lerp(v, lerp(u, _grad(p[AA], x, y, z), _grad(p[BA], x - 1, y, z)),
                  lerp(u, _grad(p[AB], x, y - 1, z), _grad(p[BB], x - 1, y - 1, z))),
             lerp(v, lerp(u, _grad(p[AA + 1], x, y, z - 1), _grad(p[BA + 1], x - 1, y, z - 1)),
                  lerp(u, _grad(p[AB + 1], x, y - 1, z - 1), _grad(p[BB + 1], x - 1, y - 1, z - 1))))
    return min(1.0, max(-1.0, r))


_NOISE_OFFSET = (0.1234, 0.5678, 0.9101)  # keeps integer inputs off the lattice, where Perlin noise is 0


def _clamp(v: float, a: float, b: float) -> float:
    lo, hi = min(a, b), max(a, b)
    return min(max(v, lo), hi)


def _smoothstep(e0: float, e1: float, x: float) -> float:
    if e1 == e0:
        return 0.0 if x < e0 else 1.0
    t = _clamp((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _interp(curve: Callable[[float], float]) -> Callable[..., object]:
    def f(t: object, *args: object) -> object:
        if len(args) == 2:
            (t0, t1), (v0, v1) = (0.0, 1.0), args
        elif len(args) == 4:
            t0, t1, v0, v1 = args
        else:
            raise ExprError("expected (t, v0, v1) or (t, tMin, tMax, v0, v1)")
        tt, a, b = to_num(t), to_num(t0), to_num(t1)
        if a > b:
            a, b, v0, v1 = b, a, v1, v0
        u = (1.0 if tt >= b else 0.0) if a == b else _clamp((tt - a) / (b - a), 0.0, 1.0)
        k = curve(u)
        return _vmap(lambda x, y: x + (y - x) * k, v0, v1)
    return f


def _spring(t: object, stiffness: object = 100.0, damping: object = 10.0, mass: object = 1.0) -> float:
    t, k, c, m = map(to_num, (t, stiffness, damping, mass))
    if not (k > 0 and m > 0 and c >= 0):
        raise ExprError("spring requires stiffness > 0, mass > 0 and damping >= 0")
    if t <= 0:
        return 0.0
    w0 = math.sqrt(k / m)
    zeta = c / (2 * math.sqrt(k * m))
    if abs(zeta - 1) < 1e-9:
        return 1 - math.exp(-w0 * t) * (1 + w0 * t)
    if zeta < 1:
        wd = w0 * math.sqrt(1 - zeta * zeta)
        return 1 - math.exp(-zeta * w0 * t) * (math.cos(wd * t) + zeta * w0 / wd * math.sin(wd * t))
    s = w0 * math.sqrt(zeta * zeta - 1)
    r1, r2 = -zeta * w0 + s, -zeta * w0 - s
    return 1 - (r2 * math.exp(r1 * t) - r1 * math.exp(r2 * t)) / (r2 - r1)


def make_wiggle(value: object, seed: int, time: float) -> Callable[..., object]:
    """Return AE-style ``wiggle(freq, amp, octaves=1, ampMult=0.5, t=time)`` = value + fractal noise offset.

    Each component of a list ``value`` gets an independent offset; the result is
    smooth in ``t`` and depends only on (seed, t, arguments).
    """
    perm, base = _perm(int(seed)), _py(value)

    def wiggle(freq: object, amp: object, octaves: object = 1.0, amp_mult: object = 0.5,
               t: object = time) -> object:
        f, a, m, tt, o = map(to_num, (freq, amp, amp_mult, t, octaves))
        n = int(_clamp(o, 1, MAX_OCTAVES)) if o == o else 1

        def offset(i: int) -> float:
            return sum(a * m ** k * _perlin(perm, tt * f * 2 ** k + _NOISE_OFFSET[0],
                                            i * 17.13 + k * 3.71 + _NOISE_OFFSET[1], _NOISE_OFFSET[2])
                       for k in range(n))
        if isinstance(base, list):
            return [to_num(c) + offset(i) for i, c in enumerate(base)]
        return (0.0 if base is None or base is UNDEFINED else to_num(base)) + offset(0)
    return wiggle


def builtin_functions(seed: int, time: float, value: object = None) -> dict[str, Callable[..., object]]:
    """Pure deterministic built-ins that need no renderer state.

    ``random`` draws are keyed by (seed, time, n) where n counts draws within the
    current expression evaluation, so the returned dict may be reused across
    evaluations. ``wiggle`` wiggles ``value`` (0 when ``None``).
    """
    seed, perm, tbits = int(seed), _perm(int(seed)), _float_bits(time)
    fallback = [0]

    def draw() -> float:
        ctx = _CURRENT.get()
        if ctx is None:
            n, fallback[0] = fallback[0], fallback[0] + 1
        else:
            n, ctx.draws = ctx.draws, ctx.draws + 1
        return _hash01(seed, tbits, n)

    def random(*args: object) -> object:
        if not args:
            return draw()
        if len(args) == 1:
            hi = args[0]
            return [draw() * to_num(c) for c in hi] if isinstance(hi, list) else draw() * to_num(hi)
        if len(args) == 2:
            return _vmap(lambda lo, hi: lo + draw() * (hi - lo), *args)
        raise ExprError("random expects (), (max) or (min, max)")

    def noise(x: object = 0.0, y: object = 0.0, z: object = 0.0) -> float:
        if isinstance(x, list):
            x, y, z = (list(x) + [0.0, 0.0, 0.0])[:3]
        return _perlin(perm, *(to_num(c) + o for c, o in zip((x, y, z), _NOISE_OFFSET)))

    return {
        "clamp": lambda v, a, b: _vmap(_clamp, v, a, b),
        "lerp": lambda a, b, u: _vmap(lambda x, y, k: x + (y - x) * k, a, b, u),
        "smoothstep": lambda e0, e1, x: _vmap(_smoothstep, e0, e1, x),
        "linear": _interp(lambda u: u),
        "ease": _interp(lambda u: u * u * (3 - 2 * u)),
        "easeIn": _interp(lambda u: u * u * (2 - u)),
        "easeOut": _interp(lambda u: 1 - (1 - u) ** 2 * (1 + u)),
        "spring": _spring,
        "noise": noise,
        "random": random,
        "wiggle": make_wiggle(value, seed, time),
    }
