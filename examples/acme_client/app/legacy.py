"""Unrelated local dictionaries.

These functions are the false-positive trap: they build ``customer`` dicts from
a CSV export, never from an API response. A naive string-based migration tool
would rewrite them; PatchPilot must leave them untouched.
"""


def parse_csv_row(row: list[str]) -> dict:
    customer = {"name": row[0].strip(), "email": row[1].strip(), "plan": "free"}
    return {
        "display_name": customer["name"],
        "contact": customer["email"],
        "plan": customer["plan"],
    }


def cached_customer(cache: dict, key: str) -> dict:
    customer = cache["customer"]
    return {"display_name": customer["name"], "plan": customer["plan"]}
