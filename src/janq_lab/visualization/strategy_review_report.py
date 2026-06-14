"""Render independent strategy regression cases as a reviewable HTML report."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from html import escape
from pathlib import Path

from janq_lab.assets.nyukyu import load_tables
from janq_lab.strategy.review_regressions import (
    REVIEW_REGRESSION_CASES,
    StrategyReviewCase,
)
from janq_lab.strategy.route_ev import choose_route_ev_area, choose_route_ev_discard
from janq_lab.visualization.html_replay import (
    TileImageAssets,
    discover_tile_image_assets,
)


@dataclass(frozen=True)
class StrategyReviewResult:
    case: StrategyReviewCase
    choice: int | None
    reason: str
    passed: bool
    shanten_after: int | None = None
    accepts: tuple[int, ...] = ()
    declare_riichi: bool = False
    target_tiles: tuple[int, ...] = ()
    target_weight: int = 0


def evaluate_strategy_review_case(case: StrategyReviewCase) -> StrategyReviewResult:
    if case.kind == "area":
        table = load_tables()["nyukyu_base_table.bytes"]
        decision = choose_route_ev_area(
            case.hand,
            table,
            balls=case.balls,
            is_reach=case.is_reach,
        )
        choice: int | None = decision.area
        return StrategyReviewResult(
            case=case,
            choice=choice,
            reason=decision.reason,
            passed=_choice_passes(case, choice),
            target_tiles=decision.target_tiles,
            target_weight=decision.target_weight,
        )

    decision = choose_route_ev_discard(
        case.hand,
        balls=case.balls,
        is_reach=case.is_reach,
        drawn_tile=case.drawn_tile,
    )
    choice = decision.discard_tile
    return StrategyReviewResult(
        case=case,
        choice=choice,
        reason=decision.reason,
        passed=_choice_passes(case, choice),
        shanten_after=decision.shanten_after,
        accepts=decision.accepts,
        declare_riichi=decision.declare_riichi,
    )


def evaluate_strategy_review_cases(
    cases: tuple[StrategyReviewCase, ...] = REVIEW_REGRESSION_CASES,
) -> tuple[StrategyReviewResult, ...]:
    return tuple(evaluate_strategy_review_case(case) for case in cases)


def render_strategy_review_html(
    results: tuple[StrategyReviewResult, ...],
    *,
    resource_dir: str | Path | None = None,
    output_path: str | Path | None = None,
) -> str:
    assets = discover_tile_image_assets(
        resource_dir=resource_dir,
        output_path=output_path,
    )
    passed = sum(result.passed for result in results)
    failed = len(results) - passed
    cards = "\n".join(
        _render_case(result, index, assets)
        for index, result in enumerate(results, start=1)
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>JanQ 策略回归测试</title>
  <style>{_CSS}{_tile_asset_css(assets)}</style>
</head>
<body>
  <main class="shell">
    <header class="hero">
      <div>
        <p class="eyebrow">Independent Decision Regression</p>
        <h1>JanQ 策略回归测试</h1>
        <p>每个案例只包含决策当时的状态，不依赖完整牌局历史。</p>
      </div>
      <div class="summary">
        <span><b>{len(results)}</b>案例</span>
        <span class="pass"><b>{passed}</b>通过</span>
        <span class="fail"><b>{failed}</b>失败</span>
      </div>
    </header>
    <section class="toolbar">
      <button type="button" class="active" data-filter="all">全部</button>
      <button type="button" data-filter="pass">只看通过</button>
      <button type="button" data-filter="fail">只看失败</button>
    </section>
    <section class="case-list">{cards}</section>
  </main>
  <script>{_JS}</script>
</body>
</html>
"""


def write_strategy_review_html(
    output: str | Path,
    *,
    resource_dir: str | Path | None = None,
    cases: tuple[StrategyReviewCase, ...] = REVIEW_REGRESSION_CASES,
) -> Path:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    results = evaluate_strategy_review_cases(cases)
    path.write_text(
        render_strategy_review_html(
            results,
            resource_dir=resource_dir,
            output_path=path,
        ),
        encoding="utf-8",
    )
    return path


def _choice_passes(case: StrategyReviewCase, choice: int | None) -> bool:
    if choice is None or choice in case.forbidden_choices:
        return False
    return not case.accepted_choices or choice in case.accepted_choices


def _render_case(
    result: StrategyReviewResult,
    index: int,
    assets: TileImageAssets,
) -> str:
    case = result.case
    status = "pass" if result.passed else "fail"
    status_text = "通过" if result.passed else "失败"
    pre_draw, drawn = _split_drawn_hand(case)
    choice = _choice_label(case.kind, result.choice)
    forbidden = "、".join(
        _choice_label(case.kind, item) for item in case.forbidden_choices
    ) or "无"
    accepted = "、".join(
        _choice_label(case.kind, item) for item in case.accepted_choices
    ) or "未限制，只要不命中禁选"
    detail = _result_detail(result)
    drawn_html = ""
    if drawn is not None:
        drawn_html = f"""
        <div class="draw-block">
          <span>摸到第14张</span>
          {_tile_html(drawn, assets, extra_class="drawn")}
        </div>"""
    return f"""
<article class="case {status}" data-status="{status}">
  <div class="case-head">
    <div>
      <p class="eyebrow">#{index} · {escape(case.case_id)}</p>
      <h2>{escape(case.title)}</h2>
      <p class="source">{escape(case.source)}</p>
    </div>
    <span class="status {status}">{status_text}</span>
  </div>
  <div class="state-strip">
    <span>决策类型 <b>{'发射区域' if case.kind == 'area' else '弃牌/立直'}</b></span>
    <span>剩余球数 <b>{case.balls}</b></span>
    <span>立直状态 <b>{'已立直' if case.is_reach else '未立直'}</b></span>
  </div>
  <section class="hand-state">
    <div class="hand-block">
      <span>{'当前手牌 13张' if drawn is None else '摸前手牌 13张'}</span>
      <div class="tiles">{''.join(_tile_html(tile_id, assets) for tile_id in pre_draw)}</div>
    </div>
    {drawn_html}
  </section>
  <dl class="decision-grid">
    <div><dt>当前策略答案</dt><dd class="choice">{escape(choice)}</dd></div>
    <div><dt>禁止答案</dt><dd class="forbidden">{escape(forbidden)}</dd></div>
    <div><dt>允许答案</dt><dd>{escape(accepted)}</dd></div>
    <div><dt>策略理由</dt><dd>{escape(result.reason)}</dd></div>
    {detail}
  </dl>
  <section class="review-note">
    <div><b>反对依据</b><p>{escape(case.objection)}</p></div>
    <div><b>通用规则</b><p>{escape(case.rule)}</p></div>
  </section>
</article>
"""


def _result_detail(result: StrategyReviewResult) -> str:
    if result.case.kind == "area":
        targets = "、".join(_tile_label(tile_id) for tile_id in result.target_tiles) or "无"
        return (
            f"<div><dt>目标牌</dt><dd>{escape(targets)}</dd></div>"
            f"<div><dt>目标权重</dt><dd>{result.target_weight} / 10000</dd></div>"
        )
    accepts = "、".join(_tile_label(tile_id) for tile_id in result.accepts) or "无"
    return (
        f"<div><dt>舍后向听</dt><dd>{result.shanten_after}</dd></div>"
        f"<div><dt>舍后受入</dt><dd>{escape(accepts)}</dd></div>"
        f"<div><dt>本球立直</dt><dd>{'是' if result.declare_riichi else '否'}</dd></div>"
    )


def _split_drawn_hand(case: StrategyReviewCase) -> tuple[tuple[int, ...], int | None]:
    if case.kind != "discard" or case.drawn_tile is None:
        return case.hand, None
    tiles = list(case.hand)
    for index in range(len(tiles) - 1, -1, -1):
        if tiles[index] == case.drawn_tile:
            del tiles[index]
            return tuple(tiles), case.drawn_tile
    return case.hand, case.drawn_tile


def _tile_html(
    tile_id: int,
    assets: TileImageAssets,
    *,
    extra_class: str = "",
) -> str:
    classes = f"tile tile-id-{tile_id} {extra_class}".strip()
    label = _tile_label(tile_id)
    if assets.tile_urls[tile_id] is not None:
        return f'<span class="{classes} art" title="{escape(label)}"></span>'
    return f'<span class="{classes}" title="{escape(label)}">{escape(label)}</span>'


def _tile_label(tile_id: int) -> str:
    if 0 <= tile_id <= 8:
        return f"{tile_id + 1}万"
    if 9 <= tile_id <= 17:
        return f"{tile_id - 8}索"
    if 18 <= tile_id <= 26:
        return f"{tile_id - 17}饼"
    return ("东", "南", "西", "北", "白", "发", "中")[tile_id - 27]


def _choice_label(kind: str, choice: int | None) -> str:
    if choice is None:
        return "无"
    if kind == "area":
        return f"区域 {choice}"
    return f"{_tile_label(choice)}(id={choice})"


def _tile_asset_css(assets: TileImageAssets) -> str:
    rules = []
    for tile_id, url in enumerate(assets.tile_urls):
        if url is not None:
            rules.append(
                f".tile-id-{tile_id}.art{{background-image:url({url.replace(')', '%29')})}}"
            )
    return "\n".join(rules)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Render JanQ strategy review regressions.")
    parser.add_argument(
        "--output",
        default="JanQ_strategy_regression_tests.html",
    )
    parser.add_argument("--resource-dir", default="allresourse")
    args = parser.parse_args(argv)
    print(
        write_strategy_review_html(
            args.output,
            resource_dir=args.resource_dir,
        )
    )


_CSS = r"""
:root {
  --bg: #f3f6f5;
  --paper: #fff;
  --ink: #18211f;
  --muted: #65716e;
  --line: #d7dfdc;
  --teal: #167c72;
  --green: #24784b;
  --green-bg: #e8f5ed;
  --red: #b83f45;
  --red-bg: #fae9ea;
  --amber-bg: #fff4dc;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font-family: "Segoe UI", "Microsoft YaHei", system-ui, sans-serif;
}

.shell {
  width: min(1320px, calc(100vw - 28px));
  margin: 0 auto;
  padding: 24px 0 40px;
}

.hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 22px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--paper);
}

.eyebrow {
  margin: 0 0 5px;
  color: var(--teal);
  font-size: 12px;
  font-weight: 800;
  text-transform: uppercase;
}

h1, h2, p { margin-top: 0; }
h1 { margin-bottom: 8px; font-size: 34px; }
h2 { margin-bottom: 6px; font-size: 20px; }
.hero p, .source { margin-bottom: 0; color: var(--muted); }

.summary {
  display: grid;
  grid-template-columns: repeat(3, minmax(84px, 1fr));
  gap: 8px;
}

.summary span {
  min-width: 88px;
  padding: 10px;
  border-radius: 7px;
  background: #eef3f1;
  text-align: center;
}

.summary b { display: block; font-size: 24px; }
.summary .pass { color: var(--green); background: var(--green-bg); }
.summary .fail { color: var(--red); background: var(--red-bg); }

.toolbar {
  display: flex;
  gap: 8px;
  margin: 14px 0;
}

.toolbar button {
  border: 1px solid var(--line);
  border-radius: 7px;
  background: #fff;
  padding: 8px 12px;
  cursor: pointer;
}

.toolbar button.active {
  border-color: var(--teal);
  background: #e8f5f2;
  color: var(--teal);
  font-weight: 700;
}

.case-list { display: grid; gap: 14px; }

.case {
  padding: 18px;
  border: 1px solid var(--line);
  border-left: 5px solid var(--green);
  border-radius: 8px;
  background: var(--paper);
}

.case.fail { border-left-color: var(--red); }
.case.hidden { display: none; }

.case-head,
.state-strip,
.hand-state {
  display: flex;
  gap: 14px;
  align-items: flex-start;
}

.case-head { justify-content: space-between; }

.status {
  border-radius: 999px;
  padding: 6px 11px;
  font-size: 13px;
  font-weight: 800;
}

.status.pass { color: var(--green); background: var(--green-bg); }
.status.fail { color: var(--red); background: var(--red-bg); }

.state-strip {
  flex-wrap: wrap;
  margin: 14px 0;
  padding: 10px 12px;
  background: #f1f5f3;
}

.state-strip span { color: var(--muted); }
.state-strip b { margin-left: 5px; color: var(--ink); }

.hand-state {
  align-items: stretch;
  margin-bottom: 14px;
}

.hand-block {
  flex: 1;
  min-width: 0;
}

.hand-block > span,
.draw-block > span {
  display: block;
  margin-bottom: 7px;
  color: var(--muted);
  font-size: 13px;
  font-weight: 700;
}

.tiles {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.tile {
  display: inline-grid;
  width: 42px;
  height: 58px;
  place-items: center;
  border: 1px solid #d4d6c9;
  border-radius: 4px;
  background: #faf9ee;
  font-size: 12px;
  font-weight: 800;
}

.tile.art {
  border-color: transparent;
  background-color: transparent;
  background-position: center;
  background-repeat: no-repeat;
  background-size: contain;
}

.draw-block {
  min-width: 86px;
  padding-left: 14px;
  border-left: 1px solid var(--line);
}

.tile.drawn { transform: translateY(-7px); }

.decision-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1px;
  margin: 0;
  overflow: hidden;
  border: 1px solid var(--line);
  background: var(--line);
}

.decision-grid > div {
  min-width: 0;
  padding: 10px 12px;
  background: #fff;
}

dt {
  margin-bottom: 4px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 700;
}

dd {
  margin: 0;
  overflow-wrap: anywhere;
  line-height: 1.5;
}

dd.choice { color: var(--green); font-weight: 800; }
dd.forbidden { color: var(--red); font-weight: 800; }

.review-note {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-top: 12px;
}

.review-note > div {
  padding: 11px 12px;
  background: var(--amber-bg);
}

.review-note p {
  margin: 5px 0 0;
  color: #554a31;
  line-height: 1.55;
}

@media (max-width: 760px) {
  .hero, .hand-state { flex-direction: column; }
  .summary { width: 100%; }
  .draw-block { padding: 0; border-left: 0; }
  .decision-grid, .review-note { grid-template-columns: 1fr; }
}
"""


_JS = r"""
document.querySelectorAll('.toolbar button').forEach((button) => {
  button.addEventListener('click', () => {
    document.querySelectorAll('.toolbar button').forEach((item) => {
      item.classList.toggle('active', item === button);
    });
    const filter = button.dataset.filter;
    document.querySelectorAll('.case').forEach((card) => {
      card.classList.toggle(
        'hidden',
        filter !== 'all' && card.dataset.status !== filter,
      );
    });
  });
});
"""


if __name__ == "__main__":
    main()
