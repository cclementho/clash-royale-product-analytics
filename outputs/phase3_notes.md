# Phase 3 notes: does the card level gap decide matches?

Numbers come from `outputs/phase3_results.txt` (src/level_gap.py).

## Setup

- **Level of a deck:** each player's average card level is their total card level divided by 8. Levels are on the 1 to 13 scale for every rarity (Phase 1).
- **Level gap:** player A's average minus player B's.
- **Model:** logistic regression of "A won" on the level gap and the trophy difference (A minus B), fit on **all 37,802,265 ladder battles**.

**Fitting on all battles in 8 GB of RAM.** The two inputs take few distinct values, so SQL groups the battles into 37,768 (trophy band, gap, trophy difference) cells, and a binomial model is fit on the cells. That gives exactly the same estimate as a logit on every battle. Standard errors are clustered by player A; the per-player sums they need are computed in SQL (8,560,244 player clusters).

**Why A and B are assigned at random.** The raw data labels each player as winner or loser. If the winner were always "A", the outcome would be 1 in every row and the gap would always be measured from the winner's side, so a model would learn the labeling instead of the game. Instead a hash of each battle's key decides whether A is the winner or the loser.

The result checks out:

- A won 49.994% of the time.
- The mean gap is -0.0001.
- The fitted intercept is -0.0002 (95% CI -0.0009 to +0.0005), which is zero as a symmetric setup requires.

## Main result

Typical gaps are small. 24.9% of battles have no gap, and 17.7% have a gap of one level or more.

| Level gap (trophy difference 0) | Win probability |
|---|---|
| -2 | 24.7% |
| -1 | **36.4%** |
| -0.5 | 43.1% |
| 0 | 50.0% |
| +0.5 | 56.9% |
| +1 | 63.6% |

- **Odds:** each +1 level multiplies the odds of winning by 1.75.
- **Trophies matter far less:** each +10 trophies multiplies the odds by 1.039. Trophy gaps are small (SD 15.8), because matchmaking pairs players on trophies.
- **The model fits the raw data well between -2.5 and +2.5 levels.** Raw win rates across all battles, both sides, are 36.0% at -1 and 64.0% at +1.

**The curve flattens beyond 2.5 levels.** Win rate peaks near 79% at +2.5 and is 76.5% at +3. Only 0.97% of battles have a gap beyond 2.5 levels.

**Hypothesis, not tested here:** at gaps this large, the usual link between trophies, skill and cards may break down. The flattening means the side with much better cards wins less often than the curve predicts. Two kinds of player would produce that:

- **Smurfs:** highly skilled players on low-level accounts. They sit on the underleveled side and win more than their cards suggest.
- **Over-upgraded players:** players whose cards are far ahead of their skill. They sit on the overleveled side and win less than their cards suggest.

Both are the skill bias below at its most extreme.

**One explanation that does not fit:** deliberately deranked players. A player who drops trophies on purpose usually has both high cards and high skill, so they would win *more* than the curve predicts and make it steeper, not flatter.

The data has no account age, player level or trophy history before Dec 7, so none of this is confirmed.

## By trophy range: the effect per level differs, and so does how often gaps happen

Separate logit per band, all battles, SEs clustered by player:

| Trophy band | Battles | Odds ratio per level | Battles with a 1+ level gap | SD of gap |
|---|---|---|---|---|
| Under 4,000 | 2,060,104 | 2.02 | 30.4% | 1.01 |
| 4,000 to 4,499 | 10,657,650 | 1.67 | 33.3% | 1.08 |
| 4,500 to 4,999 | 14,864,852 | 1.77 | 16.4% | 0.69 |
| 5,000 to 5,499 | 6,880,397 | 1.97 | 1.1% | 0.26 |
| 5,500 to 5,999 | 2,414,507 | 2.19 | 0.21% | 0.15 |
| 6,000+ | 924,755 | 2.33 | 0.06% | 0.09 |

A test that the bands share one coefficient is strongly rejected: Wald chi2 = 7,204, df = 5, p < 1e-10. It treats bands as independent, which is approximately true.

Two findings come out of this table:

- **Each level matters at every trophy range, and more at the top.** But above 5,000 trophies a one-level gap almost never happens. There, "win probability at -1" would be an extrapolation beyond the observed gaps, so the table reports odds per level instead.
- **Lopsided matches are a below-5,000 problem.** That is where 1+ level gaps actually occur (16% to 33% of battles), even though the effect per level is smallest at 4,000 to 4,999.

Why the per-level effect is larger at the top is not tested here. One possibility is that skill varies less among top players, so less of the card advantage is offset by a skill deficit. This is a hypothesis.

**Correction:** an earlier version of this analysis reported "the same effect at every trophy range". That was a bug. See "Mistakes I caught" in the README.

## Robustness: deck choice

On a 5% battle sample (1,890,848 battles), I added differences in 24 win-condition cards and in average elixir cost. The level-gap coefficient on that same sample moves from 0.562 to 0.596 (95% CI 0.591 to 0.601). Deck choice does not explain the effect.

This check uses a sample because the 24 extra columns do not fit in memory for all battles.

## Skill bias: direction and a check

**Mechanism.** In 2020 ladder matchmaking paired players on trophies. Trophies reflect both skill and card levels, so between two players at the same trophies, the one with higher card levels is likely the less skilled one.

**Direction.** Every observed level advantage comes bundled with a skill disadvantage. The model credits the level gap with the real benefit of better cards minus the cost of being the weaker player. **The estimate is biased toward zero.** The pure effect of card levels, holding skill fixed, is larger.

**Check.** If this is right, removing a player's own skill should make the level effect larger. This check uses its own sample and model, separate from the headline:

- **Sample:** all 143,117 players with 50 or more battles, every battle from their own side (14,527,369 player-battles). A battle between two such players appears once for each of them.
- **Model:** a linear probability model, estimated twice on exactly these rows. The only difference is whether player fixed effects are included. With fixed effects, each player's battles are compared only with each other, so that player's own skill drops out.

| Same 143,117 players, same rows, same linear model | Win probability per +1 level |
|---|---|
| Pooled, no player effects | +11.20 pp (95% CI +11.01 to +11.39) |
| Player fixed effects | +17.74 pp (95% CI +17.59 to +17.90) |

**Compare these two numbers with each other, not with the headline.** The headline (50% to 36.4%) comes from the logistic model on all battles with random sides; its slope at a gap of 0 is about 14 pp per level. The check answers a narrower question: does the estimate grow when own skill is removed? It grows by about 58%, as the skill-bias story predicts. The trophy coefficient barely moves (+0.62 vs +0.58 pp per 10 trophies).

**What the fixed-effects estimate still misses.** Within a player, most gap variation comes from opponents: the within-player SD is 0.385, against 0.485 overall. The opponent's skill bias remains (a higher-level opponent at the same trophies is likely less skilled), so 17.7 pp is still probably an underestimate.

**Not in the data.** King tower level likely moves with card levels, so the "level gap" effect includes tower strength. The average weights all 8 cards equally.

## What this means for the product

The card level gap is one of the strongest predictors of who wins a ladder match, and the true effect is probably larger than the headline. Below 5,000 trophies, 24.0% of battles have a gap of a full level or more (Phase 4 inputs), roughly 36/64 odds before play starts. Above 5,000, gaps are rare. A level-aware matchmaking change would mainly affect players below 5,000.
