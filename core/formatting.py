"""Presentation helpers shared by templates and form labels. No business logic."""

from decimal import Decimal, InvalidOperation


def format_quantity(value):
    """Show a stored quantity in Turkish notation without padded zeros.

    Presentation only: 80.000 -> "80", 2.500 -> "2,5", 1250.750 -> "1.250,75".
    Stored Decimal values and form parsing are unchanged.
    """
    if value is None or value == "":
        return "—"
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return value
    if not number.is_finite():
        return value
    if number == number.to_integral_value():
        number = number.quantize(Decimal(1))
    else:
        number = number.normalize()
    sign = "-" if number < 0 else ""
    integer_part, _, fraction = format(abs(number), "f").partition(".")
    groups = []
    while len(integer_part) > 3:
        groups.insert(0, integer_part[-3:])
        integer_part = integer_part[:-3]
    groups.insert(0, integer_part)
    text = sign + ".".join(groups)
    return f"{text},{fraction}" if fraction else text
