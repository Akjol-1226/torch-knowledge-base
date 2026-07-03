from app.modules.docgen.calculator import calculate


def test_calculate_arithmetic_expression():
    result = calculate("220 * (3.2 * 1.6) / (2.0 * 1.25)")

    assert result.ok is True
    assert result.result == "450.56"


def test_calculate_rejects_function_call():
    result = calculate("__import__('os').system('dir')")

    assert result.ok is False
    assert "不支持" in result.error


def test_calculate_rejects_division_by_zero():
    result = calculate("1 / 0")

    assert result.ok is False
    assert "除零" in result.error


def test_calculate_rejects_large_power():
    result = calculate("2 ** 99")

    assert result.ok is False
    assert "指数" in result.error
