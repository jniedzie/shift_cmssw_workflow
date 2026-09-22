"""Exact, bounded lowering of nested FLUKA region unions for pyg4ometry.

Positive unions are distributed, while negative unions become multiple
subtractions. Expansion limits fail closed. Complement-only output zones are
rejected because the downstream CSG implementation needs a positive solid.
"""

from dataclasses import dataclass
import re


class BooleanNormalizationError(ValueError):
    pass


@dataclass(frozen=True)
class Factor:
    positive: bool
    value: object  # body-name string or Expression


@dataclass(frozen=True)
class Expression:
    zones: tuple  # tuple of conjunctions, each a tuple of Factor


_TOKEN = re.compile(r"\s*([A-Za-z_][A-Za-z_0-9]*|[+\-()|])")
_HEADER = re.compile(r"^(\s*([A-Za-z_][A-Za-z_0-9]*)\s+([0-9]+)\s+)(.*)$")


class _Parser:
    def __init__(self, text):
        self.tokens, self.position = [], 0
        offset = 0
        while offset < len(text):
            if not text[offset:].strip():
                break
            match = _TOKEN.match(text, offset)
            if not match:
                raise BooleanNormalizationError(f"unsupported expression syntax near {text[offset:offset + 40]!r}")
            self.tokens.append(match.group(1))
            offset = match.end()

    def peek(self):
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def take(self):
        value = self.peek()
        self.position += 1
        return value

    def expression(self):
        if self.peek() == "|":
            self.take()
        zones = [self.zone()]
        while self.peek() == "|":
            self.take()
            zones.append(self.zone())
        return Expression(tuple(zones))

    def zone(self):
        factors = []
        while self.peek() not in (None, "|", ")"):
            token = self.peek()
            if token in ("+", "-"):
                positive = self.take() == "+"
                token = self.take()
            elif not factors and token not in ("(", ")"):
                positive = True  # FLUKA permits an unsigned first body.
                token = self.take()
            else:
                raise BooleanNormalizationError("expected an explicitly signed body or subzone")
            if token == "(":
                value = self.expression()
                if self.take() != ")":
                    raise BooleanNormalizationError("missing closing parenthesis")
            elif token and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", token):
                value = token
            else:
                raise BooleanNormalizationError("expected body name or parenthesized subzone")
            factors.append(Factor(positive, value))
        if not factors:
            raise BooleanNormalizationError("empty union alternative or conjunction")
        return tuple(factors)


def parse_expression(text):
    parser = _Parser(text)
    result = parser.expression()
    if parser.peek() is not None:
        raise BooleanNormalizationError(f"unexpected token {parser.peek()!r}")
    return result


def body_names(expression):
    return {name for zone in expression.zones for factor in zone
            for name in ([factor.value] if isinstance(factor.value, str) else body_names(factor.value))}


def evaluate_expression(expression, values):
    def evaluate(factor):
        value = values[factor.value] if isinstance(factor.value, str) else evaluate_expression(factor.value, values)
        return value if factor.positive else not value
    return any(all(evaluate(factor) for factor in zone) for zone in expression.zones)


def has_nested_union(expression):
    return any(not isinstance(factor.value, str) and
               (len(factor.value.zones) > 1 or has_nested_union(factor.value))
               for zone in expression.zones for factor in zone)


def _body_occurrences(zones):
    return sum(1 if isinstance(factor.value, str) else _body_occurrences(factor.value.zones)
               for zone in zones for factor in zone)


def lower_expression(expression, *, max_zones=1024, max_body_occurrences=100000):
    """Preserve complemented conjunctions to avoid unnecessary full DNF."""
    if max_zones < 1 or max_body_occurrences < 1:
        raise BooleanNormalizationError("expansion limits must be positive")

    def check(zones):
        if len(zones) > max_zones or _body_occurrences(zones) > max_body_occurrences:
            raise BooleanNormalizationError("nested-union expansion limit exceeded")
        return zones

    def product(left, right):
        if len(left) * len(right) > max_zones:
            raise BooleanNormalizationError("nested-union zone expansion limit exceeded")
        # Check the exact prospective leaf count before allocating expanded ASTs.
        count = len(right) * _body_occurrences(left) + len(left) * _body_occurrences(right)
        if count > max_body_occurrences:
            raise BooleanNormalizationError("nested-union body expansion limit exceeded")
        return [a + b for a in left for b in right]

    def positive_value(value):
        return [(Factor(True, value),)] if isinstance(value, str) else list(value.zones)

    def subtract_zone(zone):
        if len(zone) == 1:
            factor = zone[0]
            if not factor.positive:
                return positive_value(factor.value)
            return [(Factor(False, factor.value),)]
        if not any(factor.positive for factor in zone):
            # NOT(NOT A AND NOT B) = A OR B; avoid complement-only subzones.
            return check([choice for factor in zone for choice in positive_value(factor.value)])
        return [(Factor(False, Expression((zone,))),)]

    def lower(current):
        output = []
        for zone in current.zones:
            choices = [()]
            for factor in zone:
                if isinstance(factor.value, str):
                    choices = product(choices, [(factor,)])
                    continue
                inner = lower(factor.value)
                if factor.positive:
                    choices = product(choices, inner)
                else:
                    for alternative in inner:
                        choices = product(choices, subtract_zone(alternative))
            output.extend(choices)
            check(output)
        return output

    lowered = Expression(tuple(lower(expression)))

    def require_positive_solids(current):
        for zone in current.zones:
            if not any(factor.positive for factor in zone):
                raise BooleanNormalizationError("complement-only zone needs an explicit positive source solid")
            for factor in zone:
                if not isinstance(factor.value, str):
                    require_positive_solids(factor.value)

    require_positive_solids(lowered)
    return lowered


def render_expression(expression):
    return " | ".join(" ".join(("+" if factor.positive else "-") +
                                (factor.value if isinstance(factor.value, str)
                                 else "( " + render_expression(factor.value) + " )")
                                for factor in zone) for zone in expression.zones)


def normalize_nested_unions(text, *, max_zones=1024, max_body_occurrences=100000):
    """Return normalized text and per-region original/new source-line ledger.

    Line counts are preserved: a rewritten expression occupies its region's
    first line, and former continuation code is blanked. Comments are retained.
    The result is a parser input, not a native-FLUKA fixed-width card export.
    """
    lines = text.splitlines(keepends=True)

    def code(line):
        return line.split("!", 1)[0].strip()

    starts = [index for index, line in enumerate(lines)
              if code(line).split()[:1] == ["GEOBEGIN"]]
    if len(starts) != 1:
        raise BooleanNormalizationError("expected exactly one GEOBEGIN")
    ends = [index for index in range(starts[0] + 1, len(lines)) if code(lines[index]) == "END"]
    if len(ends) < 2:
        raise BooleanNormalizationError("missing body/region END delimiters")
    begin, end = ends[:2]
    headers = []
    for index in range(begin + 1, end):
        content = code(lines[index])
        if content.startswith("#"):
            raise BooleanNormalizationError("region preprocessor directives must be resolved first")
        if not content or content.startswith("*"):
            continue
        match = _HEADER.match(lines[index].split("!", 1)[0].rstrip("\r\n"))
        if match:
            headers.append((index, match))
        elif not headers:
            raise BooleanNormalizationError("region expression before its region header")
    if not headers:
        raise BooleanNormalizationError("no regions between geometry END delimiters")
    output, ledger = list(lines), []
    for ordinal, (start, header) in enumerate(headers):
        stop = headers[ordinal + 1][0] if ordinal + 1 < len(headers) else end
        fragments, code_lines = [], []
        for index in range(start, stop):
            content = header.group(4).strip() if index == start else code(lines[index])
            if content and not content.startswith("*"):
                fragments.append(content)
                code_lines.append(index)
        name = header.group(2)
        try:
            expression = parse_expression(" ".join(fragments))
            if not has_nested_union(expression):
                continue
            lowered = lower_expression(expression, max_zones=max_zones,
                                       max_body_occurrences=max_body_occurrences)
        except BooleanNormalizationError as error:
            raise BooleanNormalizationError(f"region {name}, source line {start + 1}: {error}") from error
        if body_names(expression) != body_names(lowered) or has_nested_union(lowered):
            raise BooleanNormalizationError(f"region {name}: internal normalization invariant failed")
        rewritten = render_expression(lowered)
        for index in code_lines:
            newline = "\r\n" if lines[index].endswith("\r\n") else "\n" if lines[index].endswith("\n") else ""
            comment = "!" + lines[index].split("!", 1)[1].rstrip("\r\n") if "!" in lines[index] else ""
            prefix = header.group(1) + rewritten + (" " if comment else "") if index == start else ""
            output[index] = prefix + comment + newline
        ledger.append({
            "region": name, "source_line_start": start + 1, "source_line_end": stop,
            "original": "".join(lines[start:stop]), "normalized": "".join(output[start:stop]),
            "original_top_level_zones": len(expression.zones), "normalized_top_level_zones": len(lowered.zones),
            "original_body_occurrences": _body_occurrences(expression.zones),
            "normalized_body_occurrences": _body_occurrences(lowered.zones),
            "method": "bounded positive-union distribution and negative-union subtraction",
            "limits": {"max_zones": max_zones, "max_body_occurrences": max_body_occurrences},
        })
    return "".join(output), ledger
