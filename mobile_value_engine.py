"""Motore leggero e autonomo usato dalla versione Streamlit di Atlas."""
from __future__ import annotations

from math import isfinite
from typing import Any, Mapping


MARKETS = (
    "1", "X", "2", "1X", "12", "X2", "Goal", "No Goal",
    "Over 1.5", "Under 1.5", "Over 2.5", "Under 2.5", "Over 3.5", "Under 3.5",
)


def _odd(value: object) -> float | None:
    try:
        result = float(str(value).replace(",", "."))
        return result if isfinite(result) and result > 1.01 else None
    except (TypeError, ValueError):
        return None


def _pair_probabilities(row: Mapping[str, Any], first: str, second: str) -> dict[str, float]:
    a, b = _odd(row.get(first)), _odd(row.get(second))
    if not a or not b:
        return {}
    total = 1 / a + 1 / b
    return {first: (1 / a) / total, second: (1 / b) / total} if total else {}


def _probabilities(row: Mapping[str, Any]) -> dict[str, float]:
    odds = {market: _odd(row.get(market)) for market in MARKETS}
    result: dict[str, float] = {}
    one_x_two = {key: value for key, value in odds.items() if key in {"1", "X", "2"} and value}
    if len(one_x_two) == 3:
        total = sum(1 / value for value in one_x_two.values())
        result.update({key: (1 / value) / total for key, value in one_x_two.items()})
    result.update(_pair_probabilities(row, "Goal", "No Goal"))
    for line in ("1.5", "2.5", "3.5"):
        result.update(_pair_probabilities(row, f"Over {line}", f"Under {line}"))
    # Doppie chance ricavate dall'1X2 quando il PDF non le include.
    if all(key in result for key in ("1", "X", "2")):
        result.setdefault("1X", result["1"] + result["X"])
        result.setdefault("12", result["1"] + result["2"])
        result.setdefault("X2", result["X"] + result["2"])
    return result


def analyse_rows(rows: list[Mapping[str, Any]], only_value: bool = False) -> list[dict[str, Any]]:
    """Restituisce un pronostico prudente per evento basato sulle quote caricate.

    Il valore è una stima conservativa: confronta la probabilità senza margine
    del mercato con la quota disponibile e non inventa quote assenti.
    """
    results: list[dict[str, Any]] = []
    for row in rows:
        probabilities = _probabilities(row)
        candidates: list[tuple[float, str, float, float]] = []
        for market, probability in probabilities.items():
            odd = _odd(row.get(market))
            if not odd:
                continue
            ev = probability * odd - 1
            # Preferisce mercati con buona probabilità, poi miglior EV stimato.
            score = probability + max(ev, 0) * 0.25
            candidates.append((score, market, odd, ev))
        if not candidates:
            continue
        _, market, odd, ev = max(candidates)
        probability = probabilities[market]
        is_value = ev >= 0.015
        if only_value and not is_value:
            continue
        results.append({
            "data": row.get("Data", ""), "ora": row.get("Ora", ""),
            "partita": row.get("Partita", ""), "comp": row.get("Manifestazione", ""),
            "mercato": market, "quota": round(odd, 2),
            "prob": round(probability * 100, 1), "ev": round(ev * 100, 1),
            "is_value": is_value,
        })
    return results
