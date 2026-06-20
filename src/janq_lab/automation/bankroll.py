"""Bankroll-driven JanQ bet tier selection."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BetTierDecision:
    bet: int
    reason: str


def parse_bet_ladder(value: str | list[int] | tuple[int, ...]) -> tuple[int, ...]:
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",") if item.strip()]
        if not items:
            raise ValueError("bet_ladder must contain at least one bet")
        bets = tuple(int(item) for item in items)
    else:
        bets = tuple(int(item) for item in value)
    if any(bet <= 0 for bet in bets):
        raise ValueError("bet_ladder values must be positive")
    if tuple(sorted(set(bets))) != bets:
        raise ValueError("bet_ladder must be strictly increasing with no duplicates")
    return bets


def choose_bet_tier(
    bankroll: int | None,
    bet_ladder: tuple[int, ...],
    *,
    current_bet: int | None = None,
    forced_bet: int | None = None,
    up_multiple: float = 200.0,
    down_multiple: float = 100.0,
) -> BetTierDecision:
    if not bet_ladder:
        raise ValueError("bet_ladder must contain at least one bet")
    if forced_bet is not None:
        return BetTierDecision(forced_bet, f"forced_bet:{forced_bet}")

    if bankroll is None:
        return BetTierDecision(bet_ladder[0], "bankroll_unknown")

    if current_bet in bet_ladder:
        index = bet_ladder.index(current_bet)
    else:
        index = 0

    while index > 0 and bankroll < down_multiple * bet_ladder[index]:
        index -= 1
    while (
        index + 1 < len(bet_ladder)
        and bankroll >= up_multiple * bet_ladder[index + 1]
    ):
        index += 1

    bet = bet_ladder[index]
    return BetTierDecision(
        bet,
        f"bankroll_200_100:bankroll={bankroll}:bet={bet}:up={up_multiple:g}:down={down_multiple:g}",
    )
