# Route EV strategy notes

This document describes the current route-aware strategy prototype.

## Idea

The strategy treats JanQ as a route-selection problem:

1. If a yakuman route is genuinely close, override the public baseline and chase
   it.
2. Otherwise, let the public suit/dragon heuristic handle honitsu-ish hands.
3. Preserve normal hand efficiency unless the yakuman route has enough remaining
   balls and enough probability mass in the nyukyu table.
4. Area decisions include fourth-copy protection: a tile already held as three
   copies is not just dead, because drawing it refunds one ball.

## Route gates

The prototype considers:

- Suuankou: at least four pair/triplet-like groups and missing at most four
  tiles.
- Daisangen: at least five dragon tiles and missing at most four route tiles.
- Chuuren: at least nine tiles in one suit and missing at most four route tiles.
- Kokushi: missing at most four terminal/honor requirements.
- Honitsu: detected but currently left to the public baseline for final action,
  because hard honitsu discards reduced EV in simulation.

## Current result

The first aggressive route version increased yakuman frequency but cut normal
win rate too much. The current final version is a conservative yakuman overlay
on top of the public baseline.

The important lesson from the current numbers is that route selection must be
true EV search, not simply "more yakuman good". Yakuman wins are valuable, but
the lost ordinary wins and lost hachiren entries can dominate unless the route
is very close.
