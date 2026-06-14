# Route EV strategy notes

This document describes the current route-aware strategy prototype.

## Idea

The strategy treats JanQ as a route-selection problem:

1. If a yakuman tenpai exists, never break it. The strategy may declare reach,
   but all later non-winning draws are tsumogiri.
2. If a yakuman route is genuinely close and still feasible with the remaining
   balls, override the public baseline and chase it.
3. If an ordinary tenpai exists, keep it unless a nearby yakuman route has a
   clearly better value. Low-ball ordinary tenpai declares reach.
4. Otherwise, let the public suit/dragon heuristic handle honitsu-ish hands.
5. Area decisions include fourth-copy protection: a tile already held as three
   copies is not just dead, because drawing it refunds one ball.

Discard decisions also look one shot ahead. For every candidate discard, the
strategy rebuilds the 13-tile hand, predicts the next selected area, and compares
the effective progress and protection probability in that area. This applies to
yakuman routes, honitsu routes, and ordinary hand efficiency, so an apparently
isolated tile can be kept when it makes the next area's distribution materially
better.

Area targets are not all equal:

- First-order tiles that reduce shanten receive full weight.
- Second-order shape improvements receive only a small fraction of full weight.
- A whole suit is never reported as full-value progress merely because a
  honitsu fallback exists.
- A far-away hand may lean toward its dominant suit, but off-suit first-order
  improvements remain in the comparison.

## Route gates

The prototype considers:

- Suuankou: at least four pair/triplet-like groups and missing at most four
  tiles.
- Daisangen: missing tiles are computed from the three dragon triplets plus one
  non-dragon meld and one non-dragon pair. This avoids using ordinary shanten
  as an inaccurate proxy for the outside shape.
- Chuuren: at least nine tiles in one suit and missing at most four route tiles.
- Kokushi: missing at most four terminal/honor requirements.
- Honitsu: detected but currently left to the public baseline for final action,
  because hard honitsu discards reduced EV in simulation.

## Tenpai handling

- Yakuman tenpai is locked. Discard choice must leave the yakuman waits intact.
  Declaring reach is allowed; breaking the tenpai is not.
- Reach locks future discard behavior to drawn-tile discard until agari.
- The first draw after reach is recorded as an ippatsu chance.
- Ura-dora only counts for reached hands.
- Ordinary tenpai declares reach when the remaining balls are low. With enough
  balls, the strategy can still look for improvement, but it will not casually
  dismantle a completed tenpai for a distant route.

## Hybrid fallback preservation

When two yakuman discard candidates retain the same route distance and one is
within 90% of the best projected completion probability, the strategy compares
fallback shape before chasing a tiny probability difference. It prefers an
off-suit singleton discard over breaking a strong same-suit or chiitoitsu
fallback. This keeps yakuman pressure high without needlessly destroying
honitsu, chinitsu, or seven-pairs recovery routes.

## Current result

The first aggressive route version increased yakuman frequency but cut normal
win rate too much. The current final version is a conservative yakuman overlay
on top of the public baseline.

The important lesson from the current numbers is that route selection must be
true EV search, not simply "more yakuman good". Yakuman wins are valuable, but
the lost ordinary wins and lost hachiren entries can dominate unless the route
is very close.
