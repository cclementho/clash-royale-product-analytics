# Phase 4: Experiment design for a card level gap cap in ladder matchmaking

Numbers marked [data] come from `outputs/phase4_power.txt` (src/power.py) or the Phase 2 and 3 results. Numbers marked [assumed] are not measurable in this dataset.

## The change

Today, ladder matchmaking pairs players on trophies and ignores card levels. **Treatment:** add a cap on the gap between the two players' average card levels, which widens the longer a player waits:

| Time in queue | Maximum average level gap |
|---|---|
| 0 to 10 s | at most 0.5 levels |
| 10 to 20 s | under 1.0 level |
| Over 20 s | no cap (today's behavior) |

The time thresholds are starting values to tune. The fallback guarantees no player waits forever.

**Why these thresholds** [data]:

- **A one-level gap is a lopsided match.** The underleveled player wins 36.4%, against 50% at no gap (logit on all 37.8M battles). At a 0.5-level gap the favorite wins 56.9%; at 1.0 level, 63.6%.
- **One-level gaps are common.** 17.7% of ladder battles have a gap of one level or more. Players who are one or more levels under win 29.8% of the time.
- **The true effect is probably larger.** Phase 3 showed the estimate is biased toward zero by skill: +17.7 pp per level with player fixed effects, against +11.2 pp pooled on the same 143,117 frequent players.
- **Each level matters at every trophy range.** The odds ratio per level runs from 1.67 (4,000 to 4,499) to 2.33 (6,000+). Gaps of a level or more, though, almost only happen below 5,000 trophies (table below).

**Where the cap binds.** Because today's matching ignores levels, today's gap distribution estimates the share of candidate pairings the cap would reject:

| Trophy band | Rejected at 0.5 (gaps over 0.5) | Rejected under 1.0 (gaps of 1.0 or more) |
|---|---|---|
| Under 4,000 | 53.2% | 30.4% |
| 4,000 to 4,999 | 42.9% | 23.5% |
| 5,000 to 5,999 | 3.6% | 0.9% |
| 6,000+ | 0.2% | 0.1% |

The cap almost never binds above 5,000 trophies, where most players have maxed cards. Both the benefit and the queue-time cost sit below 5,000, which covers 73.0% of battles.

## Hypothesis

**Main hypothesis:** among players below 5,000 trophies, fewer lopsided matches make ladder feel fairer and raise multi-day retention.

**Competing prediction from Phase 2:** heavy players re-queue more after losses and stop after win streaks and new highs. Fairer matches change that mix, so matches per session could dip in the short term even if retention improves. The test is built to measure both. One limit: the Phase 2 finding describes heavy players only.

## Eligibility

**Who is in the test.** Every ladder player is randomized. The target population for the main analysis is defined by **trophies at enrollment**: the player's trophies at their first battle after assignment, before the cap can affect anything. Players enrolled below 5,000 trophies are the main analysis group. In this data that is 91.1% of players active in a week (6,316,315 of 6,933,695) [data].

**Why not filter on trophies during the test.** The cap changes who wins, so it changes who climbs past 5,000. For example, underleveled players may win more often and cross 5,000 sooner. Filtering on in-test trophies would condition on something the treatment caused. The two arms would then hold different kinds of players, and the comparison would no longer be randomized. Enrollment trophies are fixed before treatment, so both arms keep the same mix.

## Metrics

**Primary: week-4 retention.** The share of enrolled players who play at least one ladder battle in days 22 to 28 after enrollment.

- Why not win rate: every match produces one win and one loss, so each arm's win rate stays at 50% by construction.
- Why not matches per day: it is too close to the short-term dip Phase 2 predicts.
- This dataset cannot measure retention (Phase 1), so its baseline is **assumed to be 70%** for already-active ladder players, with 50% and 80% as sensitivity cases.

**Secondary**

- **Ladder matches per player per day.** Its variance is measured from this data.
- **Matches per session.** Expect a possible dip.
- **Active days in days 1 to 28.**
- **Share of matches with a 1+ level gap (manipulation check).** Below 5,000 trophies the baseline is 24.0% of battles [data]. See the decision rule for the required drop.
- **Win rate of underleveled players.** These are players whose average card level is at least 0.5 below the median for their trophy band, measured before enrollment. It should rise toward 50%.

**Pre-registered segments**

Effects are reported separately for players enrolled below 5,000 trophies in three groups. Each group compares the player's average card level in the pre-period with the median of players enrolled in the same 250-trophy bin [data]:

| Segment | Share of enrolled players below 5,000 |
|---|---|
| Underleveled (0.5+ levels below the bin median) | 24.6% |
| Middle | 52.1% |
| Over-leveled (0.5+ levels above the bin median) | 23.2% |

**Why these segments.** The cap takes away the over-leveled players' edge: they will be matched against equally strong decks more often. They are also likely the heavier spenders, since higher cards cost gold and gems. A neutral or positive average could hide churn among exactly the players who pay. So:

- retention is reported per segment
- the spending guardrail is checked inside the over-leveled segment, not only overall

Each segment holds about a quarter of the arm, so its MDE is about 2.1 times the full-group MDE. That is about 0.38 pp on week-4 retention with 1 million enrolled below-5,000 players per arm and a 70% baseline.

**Guardrails**

- **Queue time:** median and 95th percentile, by trophy band and hour. Expect the cost to be concentrated below 5,000 trophies and in quiet hours.
- **Upgrade and purchase spending per player.** Fairer matching reduces the payoff of having higher cards, which may weaken the incentive to upgrade.
- **Frustration:** the rate of early quits or forfeits, and very short battles.

None of the guardrail metrics are in this dataset. They need game telemetry.

## Randomization and spillover

Players in different arms who get matched together contaminate both arms: a control player's opponent was chosen under treatment rules. Three options:

| Design | Spillover | Power | Problem |
|---|---|---|---|
| **Separate matchmaking pool per arm, players randomized** | None: arms never meet | Player-level, very high | Smaller pools mean longer queues |
| Region-level randomization | Small at borders | Very low | Few clusters, and regions differ in time zone and spending |
| Switchback (alternate the rule by hour or day) | Spills across time blocks | High for queue time | The same player sees both rules, so retention is mixed |

**Choice: separate pools, randomized by player, with equal-size arms.**

- **Why equal sizes.** Pool size changes queue time on its own. A treatment pool of 5% next to a control pool of 95% would show worse queue times in treatment even without a cap, which confounds the comparison. With equal pools, both arms carry the same liquidity penalty and the difference is the cap.
- **Proposed split:** 25% treatment pool, 25% control pool, 50% production. Comparing the control pool with production measures the pool-size penalty directly, which shows how much of the queue-time cost is the test's own artifact.
- **Why not region-level.** Region randomization inflates variance by 1 + (m - 1) x ICC, where m is players per cluster and ICC is how alike players in the same region are. Even an ICC of 0.001 with 100,000 players per region inflates variance about 101 times (both values illustrative).
- **Analysis.** Players are the randomization unit, so player-level metrics need no clustering correction. Match-level metrics (gap share, underleveled win rate) are computed per player first, then compared across arms (or analyzed with SEs clustered by player).

## Power

Standard settings: alpha 0.05 two-sided, 80% power, 2.80 combined z.

**Matches per player per day** [data]. Measured over a 7-day pre window (Dec 19 to 25) and a 7-day post window (Dec 26 to Jan 1).

- **Two variance sources.** Observed counts undercount true battles (Phase 1), so I report two populations as bounds on the real coefficient of variation:
  - **All enrolled players:** 6,933,695 players, mean 0.517 a day, SD 1.394.
  - **Well-covered heavy players:** 23,206 players, mean 10.98 a day, SD 9.17. This group was selected using Dec 19 to Jan 3, which overlaps the outcome week, so treat it only as a rough bound.
- **CUPED.** CUPED uses each player's pre-period value as a covariate. It cuts variance by 26.5% for all enrolled players (pre/post correlation 0.515) and by 12.1% for heavy players.

| Players per arm | Relative MDE, all enrolled (CUPED) | Relative MDE, heavy players (CUPED) |
|---|---|---|
| 100,000 | 2.90% | 0.98% |
| 1,000,000 | 0.92% | 0.31% |
| 2,000,000 | 0.65% | 0.22% |

**Week-4 retention** [assumed baseline]

| Assumed baseline | MDE, 100,000 per arm | MDE, 1,000,000 per arm |
|---|---|---|
| 50% | 0.63 pp | 0.20 pp |
| 70% | 0.57 pp | 0.18 pp |
| 80% | 0.50 pp | 0.16 pp |

**Scale.** This data alone saw 6.9 million distinct ladder players in one week. A 25% pool per arm should exceed 1 million players per arm, about 0.2 pp on retention.

**Segment caveat.** The main analysis group is players enrolled below 5,000 trophies. In this data that is 91.1% of active players, so its MDE is close to the full-arm figures. The per-segment MDEs above are larger.

## Duration and novelty

- **Length:** run 5 weeks. That is 1 week of enrollment plus 4 weeks of follow-up, so every enrolled player has a full week-4 retention window.
- **Avoid holidays.** This data shows a large late-December surge.
- **Season reset:** if the test crosses one, it hits both arms equally.
- **Novelty.** Players cannot see the matchmaking rule, so classic novelty effects should be small. Behavior may still adapt slowly, for example upgrade spending as players notice that higher cards help less.
- **Check over time.** Report the treatment effect week by week. If it is still moving in week 4, extend the test.
- **Holdback.** Keep a 5% holdback for 2 to 3 months after launch to catch slow effects on spending.

## Decision rule

The margins are proposals for the PM to set before launch. They are not derived from data.

- **First, the manipulation check.** Among players enrolled below 5,000, the share of matches with a 1+ level gap must be at least 50% lower in treatment than in control. Against the 24.0% baseline, that means 12% or less.

  The 50% threshold is a proposal. Under the cap, a gap of a full level or more can only happen after the 20-second fallback, so a smaller drop means most matches are falling through to the fallback and the cap is not really binding. If the check fails, the other results say little about the cap. Tune the schedule and rerun rather than ship or kill.

- **Ship** if all of these hold:
  - week-4 retention is not worse: lower 95% CI bound above -0.2 pp, and point estimate at or above 0
  - the over-leveled segment's retention is not significantly down
  - the gap share falls past the manipulation threshold, and underleveled players' win rate rises
  - p95 queue time rises by no more than the agreed limit in every trophy band
  - spending per player is not down beyond the agreed tolerance, overall and within the over-leveled segment

  A dip in matches per session on its own is not a reason to block: Phase 2 predicts it, and retention is the goal.

- **Iterate** if retention is neutral or positive but a guardrail fails:
  - Queue time breached in quiet bands or hours: widen the cap faster there.
  - Spending down: pair the change with upgrade incentives.
  - The effect only appears in some trophy bands: target those bands.

- **Kill** if any of these hold:
  - the retention CI is entirely below 0
  - spending falls beyond tolerance with no retention gain
  - over-leveled players churn significantly more and that loss is not offset elsewhere
  - queue time cannot be kept within limits at any cap setting
