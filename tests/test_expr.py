import math

import pytest

from scenerender.expr import UNDEFINED, ExprError, builtin_functions, compile_expr


def ev(src: str, seed: int = 7, time: float = 1.25, value: object = None, **env: object) -> object:
    return compile_expr(src)({**builtin_functions(seed, time, value=value), "time": time, "seed": seed, "value": value, **env})


@pytest.mark.parametrize("src, expected", [
    ("1 + 2 * 3", 7), ("(1 + 2) * 3", 9), ("2 ** 3 ** 2", 512), ("-(2 ** 2)", -4), ("(-2) ** 2", 4),
    ("10 - 4 - 3", 3), ("7 % 4 * 2", 6), ("-7 % 4", -3), ("1 << 4 >> 2", 4), ("5 & 3 | 8", 9),
    ("5 ^ 1", 4), ("~5", -6), ("-1 >>> 28", 15), ("1 + 2 < 4 == true", True), ("0x1F + .5 + 1e-3", 31.501),
    ("!0 + 1", 2), ("typeof 1 + 'x'", "numberx"),
])
def test_precedence(src, expected):
    assert ev(src) == expected


def test_unary_before_pow_is_syntax_error():
    with pytest.raises(ExprError):
        compile_expr("-2 ** 2")


def test_ternary():
    assert ev("time > 1 ? 'late' : 'early'") == "late"
    assert ev("0 ? 1 : 2 ? 3 : 4") == 3
    assert ev("1 ? 2 ? 'a' : 'b' : 'c'") == "a"


def test_strings():
    assert ev(r"""'it\'s' + "\n".length""") == "it's1"
    assert ev(r"'\x41\u0042\u{1F600}'") == "AB\U0001F600"
    assert ev("'a' + 1 + 2") == "a12"
    assert ev("1 + 2 + 'a'") == "3a"
    assert ev("'n=' + 1.5 + ',' + [1, 2]") == "n=1.5,1,2"
    assert ev("'abc'.length + 'abc'[1]") == "3b"
    assert ev("'10' == 10 && '10' !== 10 && null == undefined && null !== undefined")
    assert ev("'b' > 'a' && '10' < '9' && 10 > '9'")
    assert ev("String(3) + Number('0x10') + parseFloat('2.5px') + parseInt('42abc')") == "3162.542"
    assert ev("isNaN('x') && !isNaN('3')")


def test_logical_short_circuit():
    boom = lambda: pytest.fail("evaluated")
    assert ev("0 && boom()", boom=boom) == 0
    assert ev("'x' || boom()", boom=boom) == "x"
    assert ev("null ?? 5") == 5 and ev("0 ?? boom()", boom=boom) == 0
    assert ev("'' || 'default'") == "default"
    assert ev("1 && 'yes'") == "yes"
    assert ev("false ? boom() : 1", boom=boom) == 1


def test_division_by_zero():
    assert ev("1 / 0") == math.inf and ev("-1 / 0") == -math.inf
    assert math.isnan(ev("0 / 0")) and math.isnan(ev("5 % 0"))


def test_vector_arithmetic():
    assert ev("[1, 2] + [3, 4]") == [4, 6]
    assert ev("value - [1, 1, 1]", value=(5, 6, 7)) == [4, 5, 6]
    assert ev("[1, 2] * 3") == [3, 6] and ev("2 * [1, 2]") == [2, 4]
    assert ev("[2, 4] / 2") == [1, 2]
    assert ev("-[1, 2]") == [-1, -2]
    assert ev("[1, 2, 3].length + value[1]", value=[9, 8]) == 11
    assert ev("[1, 2][5]") is UNDEFINED
    for bad in ("[1, 2] * [3, 4]", "[1, 2] + 1", "2 / [1, 2]"):
        with pytest.raises(ExprError):
            ev(bad)


def test_math():
    assert ev("Math.max(1, 5, 3) + Math.min(4, 2)") == 7
    assert ev("Math.round(2.5) + Math.round(-2.5) + Math.round(-2.6)") == 3 - 2 - 3
    assert ev("Math.floor(-1.5) + Math.ceil(1.2) + Math.trunc(-1.7) + Math.sign(-3)") == -2 + 2 - 1 - 1
    assert ev("Math.hypot(3, 4) + Math.sqrt(16) + Math.pow(2, 10) + Math.abs(-1)") == 5 + 4 + 1024 + 1
    assert ev("Math.atan2(1, 1)") == pytest.approx(math.pi / 4)
    assert ev("Math.sin(Math.PI / 2) + Math.cos(0) + Math.exp(0) + Math.log(Math.E)") == pytest.approx(4)
    assert ev("Math.log2(8) + Math.log10(1000)") == 6
    assert ev("Math.log(0)") == -math.inf and math.isnan(ev("Math.sqrt(-1)"))
    assert ev("Math.min()") == math.inf


def test_declarations_and_final_expression():
    assert ev("var a = 2; let b = a * 3; const c = b + 1; c * 10;") == 70
    assert ev("x = 4\ny = x + 1\nx * y") == 20
    assert ev("var a = 1, b = 2; // comment\n/* block */ a + b") == 3
    assert ev("var time = 100; time + 1") == 101
    with pytest.raises(ExprError):
        compile_expr("var a = 1;")
    with pytest.raises(ExprError):
        compile_expr("1 2")


def test_env_callables():
    calls = []

    def prop(ref):
        calls.append(ref)
        return (10, 20)
    assert ev("prop('logo.position')[1] + param('speed')", prop=prop, param=lambda n: 3) == 23
    assert calls == ["logo.position"]
    assert ev("thisLayer.index + thisLayer.name.length", thisLayer={"index": 4, "name": "abc"}) == 7
    with pytest.raises(ExprError):
        ev("f(1)", f=lambda: 1)
    with pytest.raises(ExprError):
        ev("x()", x=3)


def test_random_deterministic_and_varies():
    a = ev("[random(), random(), random(10, 20), random([5, 5])]", seed=3, time=2.0)
    b = ev("[random(), random(), random(10, 20), random([5, 5])]", seed=3, time=2.0)
    assert a == b
    assert a[0] != a[1]
    assert 10 <= a[2] < 20 and all(0 <= c < 5 for c in a[3])
    assert ev("random()", seed=3, time=2.0) != ev("random()", seed=3, time=2.04)
    assert ev("random()", seed=3, time=2.0) != ev("random()", seed=4, time=2.0)


def test_random_counter_resets_per_evaluation():
    env = {**builtin_functions(1, 0.0)}
    e = compile_expr("random()")
    assert e(env) == e(env)


def test_noise_deterministic():
    a, b = ev("noise(time * 3, 2)", seed=5, time=0.7), ev("noise(time * 3, 2)", seed=5, time=0.7)
    assert a == b and -1 <= a <= 1
    assert ev("noise(time)", seed=5, time=0.7) != ev("noise(time)", seed=5, time=1.7)
    assert ev("noise([1.5, 2.5])", seed=5) == ev("noise(1.5, 2.5)", seed=5)
    samples = [ev("noise(x)", x=x / 10) for x in range(200)]
    assert all(-1 <= s <= 1 for s in samples) and max(samples) - min(samples) > 0.3


def test_wiggle_deterministic_smooth_and_varies():
    w = lambda t, **kw: ev("wiggle(2, 50)", seed=9, time=t, value=[100, 200], **kw)
    assert w(1.0) == w(1.0)
    assert w(1.0) != w(1.5)
    a, b = w(1.0), w(1.001)
    assert all(abs(x - y) < 1 for x, y in zip(a, b))
    assert a[0] - 100 != a[1] - 200
    offsets = [ev("wiggle(2, 50, 3) - value", seed=9, time=t / 10, value=0) for t in range(100)]
    assert max(offsets) - min(offsets) > 10 and all(abs(o) < 100 for o in offsets)
    assert ev("wiggle(1, 0)", value=42) == 42


def test_linear_and_ease():
    assert ev("linear(0.25, 0, 100)") == 25
    assert ev("linear(time, 1, 2, 10, 20)", time=1.5) == 15
    assert ev("linear(5, 1, 2, 10, 20)") == 20 and ev("linear(-5, 1, 2, 10, 20)") == 10
    assert ev("linear(0.5, [0, 10], [10, 30])") == [5, 20]
    assert ev("linear(1, 2, 0, 0, 10)") == 5
    assert ev("ease(0.5, 0, 10)") == pytest.approx(5)
    assert ev("ease(0.1, 0, 10)") < 1
    assert ev("easeIn(0.5, 0, 1, 0, 10)") < 5 < ev("easeOut(0.5, 0, 1, 0, 10)")
    assert ev("ease(2, 0, 1, [0, 0], [4, 8])") == [4, 8]
    with pytest.raises(ExprError):
        ev("linear(1, 2)")


def test_clamp_lerp_smoothstep_spring():
    assert ev("clamp(5, 0, 3) + clamp(-1, 0, 3)") == 3
    assert ev("clamp([5, -5], 0, 1)") == [1, 0]
    assert ev("lerp(10, 20, 0.5)") == 15 and ev("lerp([0, 0], [2, 4], 0.5)") == [1, 2]
    assert ev("smoothstep(0, 1, 0.5)") == 0.5 and ev("smoothstep(0, 1, 2)") == 1
    assert ev("spring(0)") == 0
    assert ev("spring(10)") == pytest.approx(1, abs=1e-6)
    assert ev("spring(0.3)") > 1
    assert ev("spring(0.3, 100, 20, 1)") < 1
    assert ev("spring(1, 100, 50, 1)") < 1


@pytest.mark.parametrize("src", [
    "__class__", "x.__class__", "'a'.__class__", "(1).__class__", "Math.__dict__", "Math['__class__']",
    "constructor", "'a'.constructor", "Math.constructor", "[].constructor", "Math['constructor']",
    "value.prototype", "nope", "nope + 1", "Math.nope", "time.foo", "[1].map",
])
def test_security(src):
    with pytest.raises(ExprError):
        ev(src, x={"a": 1}, value={"a": 1})


@pytest.mark.parametrize("src", ["eval('1')", "import os", "a => a", "function f() {}", "new Date()", "x = y = 1", "{}"])
def test_rejects_unsupported_syntax(src):
    with pytest.raises(ExprError):
        compile_expr(src)(builtin_functions(1, 0))


def test_step_limit():
    big = {"v": [0.0] * 600_000}
    with pytest.raises(ExprError, match="steps"):
        ev("v + v + v", **big)
    with pytest.raises(ExprError, match="steps"):
        compile_expr("1 + 1 + 1 + 1")({}, max_steps=2)
    assert compile_expr("1 + 1 + 1 + 1")({}, max_steps=3) == 4
    src = "var a = 'xxxxxxxx'; " + " ".join(f"var a{i} = {'a' if i == 0 else f'a{i-1}'} + {'a' if i == 0 else f'a{i-1}'};" for i in range(40)) + " a39.length"
    with pytest.raises(ExprError, match="steps"):
        ev(src)


def test_depth_and_size_limits():
    with pytest.raises(ExprError):
        compile_expr("(" * 1000 + "1" + ")" * 1000)
    with pytest.raises(ExprError):
        compile_expr("+".join(["1"] * 5000))
    with pytest.raises(ExprError):
        compile_expr("1" + " " * 70000)


def test_compile_is_cached():
    assert compile_expr("time * 2") is compile_expr("time * 2")
