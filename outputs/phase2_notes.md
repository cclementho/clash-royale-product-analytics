# Phase 2 notes: does losing change short-term re-queueing?

Draft text for the README. Every number comes from `outputs/phase2_results.txt` (src/requeue.py).

## Question, narrowed

The original question ("do players quit after losses?") cannot be answered with this data. Phase 1 showed that most long gaps hide battles that were never collected. What the data can answer: after a ladder battle, does the player start another ladder battle within 10 minutes (30 minutes as a robustness check), and does that depend on whether they just won or lost?

## Who this describes

24,791 players, selected on two things:

1. At least 50 consecutive-battle pairs between Dec 19 and Jan 3.
2. At least 90% of those pairs with an intact trophy chain, meaning few missing battles.

They are 24,791 of the 119,433 players with 51 or more battles in that period. They are heavy ladder players whose battle logs were captured well. The results say nothing about casual players, about players who left the game, or about long-term churn. Selecting on an intact chain could also favor players who play in a particular way, for example in long sessions whose logs were fetched often.

## How the outcome handles missing battles

Each battle gets one of three labels:

- **Certain yes:** the next observed battle came within 10 minutes. If a battle was missed in between, it also came within 10 minutes, so the answer is yes either way.
- **Certain no:** the next observed battle came more than 10 minutes later and the trophy chain is intact, so nothing was missed.
- **Unknown:** the gap is over 10 minutes and the chain is broken, or there is no later battle.

For this group, 4.3% of battles after a loss and 4.8% after a win are unknown. The lower bound counts unknowns as no and the upper bound counts them as yes.

Same-second collisions (one player in two battles at the same second) are dropped as collection artifacts. A rare blind spot: two missed battles whose trophy changes exactly cancel would leave the chain looking intact.

## Results

**Main comparison.** For each player we take their re-queue rate after losses minus their rate after wins, then average across players. Each player counts once. 95% confidence intervals come from 2,000 bootstrap resamples of players, not battles, because one player's battles are not independent.

| Window | Loss minus win, certain outcomes only | 95% CI | Worst-case bounds |
|---|---|---|---|
| 10 min | +6.50 pp | +6.18 to +6.83 | +1.22 to +11.22 |
| 30 min | +5.90 pp | +5.60 to +6.20 | +0.96 to +10.21 |

The worst-case bounds assume every unknown resolves against the finding, in both directions at once. They stay above zero (+0.91 at the edge of the CI for 10 minutes). Heavy players are more likely to queue again right after a loss than after a win.

**By streak (10 min, within-player, loss streak k minus win streak k)**

| Streak | Difference | 95% CI | Worst-case bounds |
|---|---|---|---|
| 1 | +3.57 pp | +3.26 to +3.90 | +1.24 to +5.68 |
| 2 | +6.87 pp | +6.46 to +7.26 | +4.51 to +8.79 |
| 3+ | +10.82 pp | +10.37 to +11.26 | +8.00 to +12.84 |

The effect does not change direction at longer streaks. It grows. The levels show where the growth comes from:

| Streak | Re-queue after losses (bounds) | Re-queue after wins (bounds) |
|---|---|---|
| 1 | 72.5 to 74.6% | 68.9 to 71.3% |
| 2 | 71.2 to 73.1% | 64.3 to 66.7% |
| 3+ | 71.6 to 73.6% | 60.8 to 63.6% |

These levels are averaged over players who have each streak type. Re-queueing after losses is flat no matter how many losses in a row. Re-queueing after wins falls with each extra win. 6.6% of battles have an unknown streak because a link they depend on is broken, and they are left out of the streak analysis.

## Chasing lost trophies vs stopping on a win

Both explanations predict the main result: more re-queueing after a loss than after a win.

**What the data cannot do:** ladder battles here are all wins or losses, with no neutral outcome to compare against. So the loss-minus-win gap by itself cannot say whether losses push people to keep playing or wins make them stop.

**What it can do:** the two stories make different predictions within each outcome.

- **Chasing** predicts that re-queueing after a loss rises as the player falls further behind. It does not: re-queueing after losses is flat across loss streaks. And re-queueing after a loss that leaves the player below their session-start trophies is no different from one that does not: +0.09 pp (95% CI -0.20 to +0.38). Here a session is a run of battles with no gap of 60 minutes or more, and this check uses the 3rd or later battle of a session.
- **Stopping on a win** predicts that re-queueing falls after good runs and peaks. It does: it falls with each extra win in a streak. After a win that sets a new session high, re-queueing is 7.81 pp lower than after other wins (95% CI -8.09 to -7.55; worst-case bounds -10.12 to -4.46).

The evidence fits "players stop on a high" better than "players chase losses". It is not proof:

- A new session high and a long win streak are largely the same event, so these are not independent confirmations.
- The 60-minute session rule is a choice.
- Hidden states that change within a session could matter. Controlling for session position and elapsed time does not change the results (see Robustness checks), but those controls are only proxies for fatigue.
- Timestamps mark battles, not queue presses. A winner and a loser share each battle's length, but this group does not contain both sides of every battle, so length differences by result cannot be fully ruled out.

## Robustness checks

**Fatigue.** Do the effects just reflect players getting tired as a session goes on? I re-estimated the comparisons as a linear probability model with player fixed effects, adding controls for:

- battle position in the session: 1, 2, 3-5, 6-10, 11-20, 21+
- minutes into the session: 0-15, 15-30, 30-60, 60-120, 120+

SEs are clustered by player. This model weights battles equally, so the fair comparison is the same model with and without the controls.

| 10-min re-queue (unknown counted as no) | No controls | With fatigue controls |
|---|---|---|
| Loss vs win | +6.41 pp (+6.10, +6.72) | +6.51 pp (+6.20, +6.82) |
| Win streak 3+ vs win streak 1 | -8.12 pp (-8.37, -7.88) | -7.92 pp (-8.16, -7.69) |
| Loss streak 3+ vs loss streak 1 | -0.85 pp (-1.05, -0.66) | -1.19 pp (-1.38, -1.00) |
| New session high vs other wins | -7.73 pp (-7.96, -7.51) | -7.61 pp (-7.83, -7.38) |

Counting unknowns as yes gives the same picture: new session high -6.82 pp (-7.04, -6.60) with controls, and win streak 3+ vs 1 -7.59 pp (-7.82, -7.37). Session position and time do not explain the win-streak or session-high effects. One new detail: long loss streaks come with slightly *less* re-queueing than single losses (about -1 pp), which again does not fit the chasing story.

**Season end (Jan 1 to Jan 3 excluded).** This uses the same players, with outcome battles from Dec 19 to Dec 31 only.

| 10 min, loss minus win | Certain outcomes only | 95% CI | Worst-case bounds |
|---|---|---|---|
| All streaks | +7.40 pp | +7.04 to +7.75 | **-1.80 to +15.84** |
| Streak 1 | +4.31 pp | +3.93 to +4.69 | +1.01 to +7.20 |
| Streak 2 | +8.07 pp | +7.57 to +8.58 | +5.27 to +10.17 |
| Streak 3+ | +12.39 pp | +11.84 to +12.94 | +9.13 to +14.71 |

The pattern holds and the point estimates are slightly larger. Win-streak re-queue falls from 66.4% to 61.7% to 57.9% (lower bounds) while loss-streak re-queue stays near 70%.

One part weakens. More outcomes are unknown before Jan 1: the bound on each rate is about 8 to 9 pp wide here, against about 5 pp for the full period, consistent with collection becoming more complete over time (Phase 1, check S8). So the worst-case bounds on the overall loss-vs-win gap now include zero. In this window, the overall gap holds only if unknown outcomes are not heavily stacked against it. The streak 2 and 3+ results stay positive even under the worst case.

## What this can and cannot show causally

The within-player design removes stable differences between players: skill, how much they play, time zone. A single battle's result is close to a coin flip once matchmaking has paired players (51.5% wins in this group), which makes the loss-versus-win contrast fairly clean. Streaks are different. A streak is itself a sequence of results, so it can line up with states we cannot see, such as tiredness or tilt. The streak and reference-point results are descriptive patterns, not clean causal estimates.

## Carry into Phase 4

If heavy players keep queueing after losses and stop after win streaks and new highs, a change that makes matches fairer could lower short-term play. Fairer matches mean fewer lopsided games and possibly fewer long win streaks, so the effect on short-term re-queueing could go either way. Phase 4 must treat this as the key tradeoff:

- Short-term session engagement may fall even if the change is good for players.
- The primary metric should be retention over days and weeks, not re-queue rate alone.
- The design should check for a short-term dip.
