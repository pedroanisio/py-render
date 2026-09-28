"""Expressions evaluated for many instances at once (object3D/@instances).

An instanced object evaluates its property expressions once per copy with a different `index`, which
costs one interpretation per copy, property and shutter sample (a smoke trail of 276 puffs: ~1700 per
frame). For the subset of the language that such expressions use, compile() builds a function that
evaluates every copy in one pass over NumPy arrays, with `index` an array.

The subset: numbers, true/false, NaN/Infinity, the names time, frame, index, count, seed and value
(when numeric), declarations and assignments, arrays of constants subscripted by an expression,
unary - + !, + - * / % ** (numbers and booleans only), comparisons, &&, ||, ?: and the pure Math
functions and constants. Anything else (strings, calls to other functions, members of other objects,
arrays as values, bitwise operators) makes compile() return None, and a subscript outside its array
(`undefined` in ECMAScript) makes the evaluation return None: the caller then interprets every copy
with expr as before. The arithmetic is expr's (the same float64 operations and the special cases of
_div, _mod and _pow); Math.sin and the like may differ from the math module in the last bit.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np

from . import expr as E

NUM, BOOL = "num", "bool"          # value kinds; booleans are carried as 0.0 / 1.0

_NAMES = ("time", "frame", "index", "count", "seed", "value")


class _Unsupported(Exception):
    pass


class _Undefined(Exception):
    pass


def _truthy(v: np.ndarray) -> np.ndarray:
    return (v == v) & (v != 0)


def _div(a, b):
    with np.errstate(divide="ignore", invalid="ignore"):
        q = a / b
    zero = b == 0
    if np.any(zero):
        inf = np.copysign(np.inf, a) * np.copysign(1.0, b)
        q = np.where(zero, np.where((a == 0) | (a != a), np.nan, inf), q)
    return q


def _mod(a, b):
    with np.errstate(invalid="ignore"):
        r = np.fmod(a, b)
    r = np.where(np.isinf(b), a, r)
    return np.where((b == 0) | ~np.isfinite(a) | (b != b), np.nan, r)


def _pow(a, b):
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        p = np.power(a, b)
    p = np.where((a == 0) & (b < 0), np.inf, p)
    p = np.where((np.abs(a) == 1) & np.isinf(b), np.nan, p)
    p = np.where(b == 0, 1.0, p)
    return np.where(b != b, np.nan, p)


def _minmax(f, empty):
    def g(*xs):
        if not xs:
            return np.asarray(empty)
        out = xs[0]
        for x in xs[1:]:
            out = f(out, x)          # np.minimum / np.maximum propagate NaN, as expr's min / max
        return out
    return g


def _floorish(f):
    return lambda x: np.where(np.isfinite(x), f(x), x)


def _errs(f):
    def g(*xs):
        with np.errstate(all="ignore"):
            return f(*xs)
    return g


_MATH_FN = {
    "abs": np.abs, "sin": np.sin, "cos": np.cos, "tan": np.tan, "asin": np.arcsin, "acos": np.arccos,
    "atan": np.arctan, "atan2": np.arctan2, "sqrt": np.sqrt, "exp": np.exp, "hypot": np.hypot, "pow": _pow,
    "floor": _floorish(np.floor), "ceil": _floorish(np.ceil), "trunc": _floorish(np.trunc),
    "round": _floorish(lambda x: np.floor(x + 0.5)),
    "sign": lambda x: np.where(x != x, x, np.sign(x)),
    "min": _minmax(np.minimum, np.inf), "max": _minmax(np.maximum, -np.inf),
}
_MATH_CONST = {k: v for k, v in E._MATH.items() if isinstance(v, float)}
_UNARY_BP = E._UNARY_BP
_LBP = E._LBP


class _Parser:
    """Pratt parser over expr's tokens (same binding powers) building closures env -> (kind, array)."""

    def __init__(self, src: str):
        self.toks = E._tokenize(src)
        self.i = 0

    def peek(self):
        return self.toks[self.i]

    def next(self):
        t = self.toks[self.i]
        self.i += 1
        return t

    def is_op(self, val, t=None) -> bool:
        t = t or self.peek()
        return t.kind == "op" and t.val == val

    def expect(self, val):
        if not self.is_op(val):
            raise _Unsupported(val)
        self.next()

    def program(self):
        stmts = []
        while self.peek().kind != "eof":
            if self.is_op(";"):
                self.next()
                continue
            t, t2 = self.peek(), self.toks[self.i + 1]
            if t.kind == "id" and t.val in E._DECL:
                self.next()
                while True:
                    name = self.next()
                    if name.kind != "id" or name.val in E._RESERVED:
                        raise _Unsupported("declaration")
                    if not self.is_op("="):
                        raise _Unsupported("declaration without a value")
                    self.next()
                    stmts.append(("decl", name.val, self.expr()))
                    if not self.is_op(","):
                        break
                    self.next()
            elif t.kind == "id" and self.is_op("=", t2):
                if t.val in E._RESERVED:
                    raise _Unsupported("assignment")
                self.next()
                self.next()
                stmts.append(("assign", t.val, self.expr()))
            else:
                stmts.append(("expr", None, self.expr()))
            end = self.peek()
            if not (self.is_op(";", end) or end.nl):
                raise _Unsupported("statement end")
        if not any(kind != "decl" for kind, _, _ in stmts):
            raise _Unsupported("no result")
        return stmts

    def expr(self, rbp: int = 0):
        left = self.prefix(self.next())
        while (t := self.peek()).kind == "op" and _LBP.get(t.val, 0) > rbp:
            left = self.infix(self.next(), left)
        return left

    def prefix(self, t):
        if t.kind == "num":
            v = float(t.val)
            return lambda env, loc: (NUM, v)
        if t.kind == "id":
            name = t.val
            if name in ("true", "false"):
                v = 1.0 if name == "true" else 0.0
                return lambda env, loc: (BOOL, v)
            if name in ("NaN", "Infinity"):
                v = float("nan") if name == "NaN" else float("inf")
                return lambda env, loc: (NUM, v)
            if name == "Math":
                return self.math()
            if name in E._RESERVED:
                raise _Unsupported(name)

            def lookup(env, loc):
                if name in loc:
                    return loc[name]
                if name in env and env[name] is not None:
                    return NUM, env[name]
                raise _Unsupported(f"name {name}")
            return lookup
        if t.kind == "op":
            if t.val == "(":
                inner = self.expr()
                self.expect(")")
                return inner
            if t.val == "[":
                return self.const_array()
            if t.val in ("-", "+", "!"):
                return self.unary(t.val)
        raise _Unsupported(repr(t.val))

    def math(self):
        self.expect(".")
        k = self.next()
        if k.kind != "id":
            raise _Unsupported("Math member")
        if k.val in _MATH_CONST and not self.is_op("("):
            v = _MATH_CONST[k.val]
            return lambda env, loc: (NUM, v)
        f = _MATH_FN.get(k.val)
        if f is None or not self.is_op("("):
            raise _Unsupported(f"Math.{k.val}")
        self.next()
        args = []
        while not self.is_op(")"):
            args.append(self.expr())
            if not self.is_op(","):
                break
            self.next()
        self.expect(")")
        g = _errs(f)
        return lambda env, loc: (NUM, g(*(np.asarray(a(env, loc)[1], np.float64) for a in args)))

    def const_array(self):
        items = []
        while not self.is_op("]"):
            neg = False
            if self.is_op("-"):
                self.next()
                neg = True
            t = self.next()
            if t.kind != "num":
                raise _Unsupported("array element")
            items.append(-float(t.val) if neg else float(t.val))
            if not self.is_op(","):
                break
            self.next()
        self.expect("]")
        if not self.is_op("["):
            raise _Unsupported("array value")
        self.next()
        index = self.expr()
        self.expect("]")
        arr = np.asarray(items, np.float64)
        n = len(arr)

        def subscript(env, loc):
            _, k = index(env, loc)
            k = np.asarray(k, np.float64)
            ok = (k >= 0) & (k < n) & (k == np.floor(k))
            if not np.all(ok):
                raise _Undefined()          # `undefined` in some copy: interpret them one by one
            return NUM, arr[k.astype(np.int64)]
        return subscript

    def unary(self, op):
        x = self.expr(_UNARY_BP)
        if self.is_op("**"):
            raise _Unsupported("unary before **")
        if op == "-":
            return lambda env, loc: (NUM, -np.asarray(x(env, loc)[1], np.float64))
        if op == "+":
            return lambda env, loc: (NUM, x(env, loc)[1])
        return lambda env, loc: (BOOL, np.where(_truthy(np.asarray(x(env, loc)[1], np.float64)), 0.0, 1.0))

    def infix(self, t, left):
        op = t.val
        if op == "?":
            then = self.expr()
            self.expect(":")
            other = self.expr(_LBP["?"] - 1)

            def ternary(env, loc):
                c = _truthy(np.asarray(left(env, loc)[1], np.float64))
                (ka, a), (kb, b) = then(env, loc), other(env, loc)
                return (ka if ka == kb else "mix"), np.where(c, a, b)
            return ternary
        if op in (".", "[", "(", "??", "&", "|", "^", "<<", ">>", ">>>"):
            raise _Unsupported(op)
        right = self.expr(_LBP[op] - 1 if op == "**" else _LBP[op])
        if op in ("&&", "||"):
            def logical(env, loc):
                (ka, a), (kb, b) = left(env, loc), right(env, loc)
                c = _truthy(np.asarray(a, np.float64))
                v = np.where(c, b, a) if op == "&&" else np.where(c, a, b)
                return (ka if ka == kb else "mix"), v
            return logical
        arith = {"+": np.add, "-": np.subtract, "*": np.multiply, "/": _div, "%": _mod, "**": _pow}.get(op)
        if arith is not None:
            f = _errs(arith)
            return lambda env, loc: (NUM, f(np.asarray(left(env, loc)[1], np.float64),
                                            np.asarray(right(env, loc)[1], np.float64)))
        cmp = {"<": np.less, ">": np.greater, "<=": np.less_equal, ">=": np.greater_equal,
               "==": np.equal, "!=": np.not_equal, "===": np.equal, "!==": np.not_equal}[op]
        strict = op in ("===", "!==")

        def compare(env, loc):
            (ka, a), (kb, b) = left(env, loc), right(env, loc)
            if strict and (ka != kb or ka == "mix"):
                raise _Unsupported("strict equality across kinds")
            with np.errstate(invalid="ignore"):
                return BOOL, cmp(a, b).astype(np.float64)
        return compare


@lru_cache(maxsize=4096)
def compile(src: str):
    """A function (env, n) -> float64 array of the n values, or None when src is outside the subset.
    env maps time, frame, index (an array), count, seed and value (None when not a number)."""
    try:
        stmts = _Parser(src).program()
    except (_Unsupported, E.ExprError):
        return None

    def run(env: dict, n: int):
        loc: dict = {}
        result = None
        try:
            for kind, name, node in stmts:
                v = node(env, loc)
                if kind != "expr":
                    loc[name] = v
                if kind != "decl":
                    result = v
        except (_Unsupported, _Undefined):
            return None
        return np.broadcast_to(np.asarray(result[1], np.float64), (n,)).copy()
    return run
