from app.tools.base import Tool


class CalculatorTool(Tool):
    name = "calc"
    description = "Evaluate a math expression (e.g. 2+3*4, sqrt(144), round(pi,2))"

    def run(self, input: str = ""):
        expr = input.strip()
        if not expr:
            return "0"

        import math

        allowed = {
            "__builtins__": {},
            "abs": abs,
            "round": round,
            "min": min,
            "max": max,
            "pow": pow,
            "sum": sum,
            "sqrt": math.sqrt,
            "pi": math.pi,
            "e": math.e,
            "sin": math.sin,
            "cos": math.cos,
            "tan": math.tan,
            "log": math.log,
            "log10": math.log10,
            "ceil": math.ceil,
            "floor": math.floor,
        }

        try:
            result = eval(expr, allowed, {})
            return str(result)
        except Exception as e:
            return f"Error: {e}"
