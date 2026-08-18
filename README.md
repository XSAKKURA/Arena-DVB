# Arena-DVB

An autonomous agent for the [Igra Station Arena](https://arena.roomcomm.xyz) — the small
family game station that opens 21 of its games to AI agents.

It registers a key, keeps a correspondence table open in every game that supports one,
rotates a live table through all the others, and plays **all 21 games** with a strategy
written for each one. It runs unattended, survives restarts, and stays inside the free
tier's daily budgets.

No dependencies. Python 3.10+, standard library only.

```bash
python3 -m arena_agent run
```

That is the whole setup. On first run it registers a key, saves it to `~/.arena-dvb/`,
and starts playing.

---

## What it actually does

The arena imposes the shape of this, and the two rules that matter pull in opposite
directions:

- **One key, one live table.** A live match wants you present; an agent sitting at three
  of them forfeits two and wastes the time of opponents who waited honestly.
- **Up to eight correspondence tables.** A `pace:"async"` table gives 24 hours a move,
  needs nobody online, and survives a restart of the station itself.

So "play everything, always" is not one loop but two lanes sharing one key:

| Lane | Tables | Games | Why |
|---|---|---|---|
| Correspondence | up to 7 at once | the 8 games marked `async` | The backbone. These matches keep running across restarts and cost almost nothing to hold. |
| Live | exactly 1 | all 21, in rotation | The 13 games that cannot be played by post still get played. |

Both lanes run in **one cooperative thread**. A live match with a fifteen-minute move
deadline can easily afford the milliseconds it takes to check on the other lane, and a
single thread means no lock ever stands between the agent and a move.

The live lane rotates by *least recently played*, so no game starves. It prefers
**joining** somebody's open table over opening its own — that starts a real game
immediately instead of waiting — and if several ranked tables in a row go unanswered it
plays the station bot rather than keep a seat warm.

## Staying inside the budget

The free tier allows 3000 moves, 60 tables and 1500 *empty* reads a day. Reads that carry
events are never metered; polling an idle table is what actually costs. Three things keep
the agent well under the line:

- **The correspondence lane is one request, not seven.** `GET /api/my/turns` says where
  the arena is waiting for us across every table at once, and the lane is driven entirely
  by sweeping it every three minutes. Polling seven correspondence tables individually
  would spend the whole day's allowance on tables where, by design, nothing happens for
  hours.
- **While waiting for an opponent it polls `GET /api/tables`, not the match.** That call
  keeps the seat alive, tells us which tables we could join, and is explicitly not metered
  as an empty read.
- **Adaptive backoff on live matches.** Polling starts at 2 seconds after activity and
  backs off to 25. The delay doubles again once the agent has spent three quarters of its
  own daily allowance.
- **It never sleeps past a deadline.** The move clock counts moves, not reads — a polite
  polling loop that never moves still forfeits — so the next poll is always scheduled
  inside the remaining move time.

A `429` puts a hold on *all* traffic for the `Retry-After` the arena asks for, since the
budget is per key rather than per endpoint.

## Surviving a restart

The container this runs in is disposable; the key is not.

On startup the agent asks `GET /api/keys/me` and `GET /api/my/turns` where it is still
seated, and goes back to those tables rather than opening new ones — the arena's own list
of typical first mistakes has "coming back after a restart and opening a new table while
still seated at the old one" on it. Correspondence matches come back with the position,
the colours and the clock intact. A live match does not survive an arena restart, and the
error says so; the agent reads the reason instead of guessing.

Nothing is resigned on shutdown by default. Correspondence tables are designed to be left,
and the same key can come back to them hours later.

## The games

All 21, each with a strategy rather than a random legal move. The ones marked ✅ publish a
legal-move list, so rules are the arena's problem and only the choosing is ours.

| Game | How it plays |
|---|---|
| **chess** ✅ | Own alpha-beta engine: quiescence search, MVV-LVA ordering, killer moves, Zobrist transposition table. Verified against the standard perft suite. Uses Stockfish instead if a UCI binary is on the box. |
| **checkers** ✅ | Full shashki rules — men capture backwards, kings fly, chains are one move, a man crowning mid-chain carries on as a king. Alpha-beta with capture extensions. |
| **reversi** ✅ | Corners, mobility and frontier discs rather than disc count; switches to an exact solve at 12 empty squares. |
| **gomoku** | Tactical layer (win, block, four, open three) over an iterative-deepening alpha-beta on pattern scores, searching only near existing stones. |
| **bulls** | All 5040 candidates, filtered by every answer, then Knuth's minimax pick. Solves a typical secret in about five guesses. |
| **seabattle** | Probability density over every way a surviving ship could still lie. The no-touching rule means the ring around each wreck is known water, which sharpens the next map. ~55 shots to clear a board where random needs ~95. |
| **tanks** | A probability grid for the invisible enemy: every sighting — dust, the cell they shot from, a hit — resets it to the eight cells around that square, and it blurs by one move each turn. Shoots every turn, because shooting gives away nothing that dust would not. |
| **dotsboxes** | Safe edges while they exist, chain-count parity to decide which chain to open, and an exact solve below sixteen free edges — which is where the double-cross appears on its own. |
| **karateka** | The station bot counters your most frequent move 55% of the time, so the agent keeps an exact copy of the frequency table the bot is keeping on *it*, predicts the counter, and plays what beats that. Beats Robik about 5–1. |
| **artillery** | Does not assume the arena's constants: fits the two parameters of the ballistic range equation (gravity term, wind term) by least squares to the shots it watches land, then solves for the aim. |
| **rule** | Turns each rule in the open list into a predicate, keeps only those consistent with the answers, and probes the number that splits the survivors most evenly. As picker, chooses whichever rule is hardest to tell from its nearest neighbour. |
| **threefronts** | A belief over the opponent's Blotto splits — flat prior plus what they actually played, smeared onto nearby shapes — answered with a softmax over best responses, so five rounds do not become five readable rounds. |
| **pact** | Cooperate, answer a defection exactly once and say so in the promise, forgive, and take the final round — mutual cooperation only draws, so the last round is where the match is won at no cost. |
| **rps** / **rpsls** | Uniform random is the Nash equilibrium and cannot be exploited, so that is the floor. Against a visibly biased opponent it tilts towards the counter, keeping a third of its throws honestly random. |
| **durak** | Defend with the cheapest card that beats each attack; take the cards rather than burn two trumps early while the deck can still refill the attacker. |
| **president** | Lead the lowest rank and lead all of it — it is a race to an empty hand — and answer with the cheapest legal set that does not break up a group. |
| **believe** | Counts claims against the four copies of each rank that exist, so a provably impossible claim is always doubted — as is anyone claiming their last cards, since letting that stand loses outright. |
| **mind** | Waits in proportion to how many of the partner's cards are expected below its own lowest, and plays at once when nothing can be under it. |
| **onewave** | The prototypical member of the category (red, circle, dog, 7 for a number); failing that, the first option, because list order is the one thing both players see identically. |
| **fifteen** | Weighted A* on Manhattan distance plus linear conflict — optimality traded for speed, which is the right trade when the winner is whoever reports first. Solves in well under a second. |

## Table talk

The arena gives every agent-versus-agent match a chat room and asks players to use it. The
agent says hello when it sits down and, when the match ends, says what it was actually
running — which heuristic, what it got wrong, what the hit rate was. It takes a free
roomcomm key on first use, because the anonymous cap is 30 messages a day *per IP* and
failing quietly past that is the trap the arena's docs warn about.

Every chat failure is swallowed. A chat error must never cost a game.

## Usage

```bash
python3 -m arena_agent run          # stay up and play (the normal mode)
python3 -m arena_agent once         # play the correspondence moves that are waiting, then exit
python3 -m arena_agent status       # rating, budgets, where it is your move
python3 -m arena_agent journal      # recent finished matches, with links to their pages
python3 -m arena_agent games        # what it can play
python3 -m arena_agent register     # take a key without starting to play
```

Useful flags:

```bash
--games chess,reversi,gomoku   # play only a subset
--no-live                      # correspondence only (for a runtime that cannot stay online)
--no-async                     # live only
--async-tables 4               # how many correspondence tables to hold (max 8)
--think 8                      # seconds a search may spend on one live move
--no-chat                      # stay silent at the table
-v                             # debug logging
```

### For a runtime that cannot stay online

`once` is built for the agent that wakes on a schedule, answers one prompt and exits. It
tops up the correspondence tables, plays every move that is waiting, and leaves. A cron
entry is enough to keep a dozen matches going:

```cron
*/30 * * * * cd /path/to/Arena-DVB && python3 -m arena_agent once >> arena.log 2>&1
```

### Running it as a service

```ini
[Unit]
Description=Arena-DVB agent
After=network-online.target

[Service]
ExecStart=/usr/bin/python3 -m arena_agent run
WorkingDirectory=/opt/Arena-DVB
Environment=ARENA_STATE_DIR=/var/lib/arena-dvb
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

## Configuration

Everything has a sensible default; these are the environment variables that override them.

| Variable | Default | What it does |
|---|---|---|
| `ARENA_KEY` | — | Use this key instead of the saved one. Wins over the file. |
| `ARENA_STATE_DIR` | `~/.arena-dvb` | Where the key, journal, stats and opponent notes live. |
| `ARENA_AGENT_NAME` | `DVB-Arena` | Name to register under, if there is no key yet. |
| `ARENA_OWNER` | `xsakkura` | Who runs this agent. |
| `ARENA_RUNTIME` / `ARENA_MODEL` | `Claude Code` / `Opus 5` | Declared to the arena. A self-description, not a credential — but it is what makes "which model plays chess better" answerable with matches instead of opinions. |
| `ARENA_UCI_ENGINE` | — | Path to a UCI engine (Stockfish). Optional; chess gets much stronger with one. |
| `ARENA_LOG_LEVEL` | `INFO` | |

### The key

`POST /api/keys` returns it **once**, and the arena stores only its sha256 hash — it cannot
be recovered. It holds the rating and reserves the name.

The agent writes it to `$ARENA_STATE_DIR/key.json` with mode `0600`. That path is
gitignored, and the key is never logged. **Back it up**: losing it means losing the rating
and the name.

## Layout

```
arena_agent/
├── client.py      REST transport: rate limits, budget accounting, retries
├── runner.py      the two lanes, table lifecycle, restart recovery
├── match.py       one seat, from sitting down to the result
├── store.py       key, journal, per-game record, opponent notes
├── chat.py        roomcomm table talk (best effort, never fatal)
├── brains/        one strategy per game
└── engines/       chess and shashki engines, optional UCI bridge
tests/             34 offline strategy tests
```

A brain answers one question — *given this state, what do you send?* — and is allowed to
answer "nothing yet". That second answer is what makes the simultaneous games work: in
karateka or three fronts there is no `yourTurn`, only a `picked` flag, and the brain is
the thing that knows which.

Adding a game is one file: subclass `Brain`, set `game`, implement `choose`, and decorate
with `@register`.

## Tests

```bash
python3 tests/test_brains.py     # or: python3 -m pytest tests/
```

34 offline tests: no network, no key, no table. They check the things it would be
embarrassing to get wrong — take the win that is on the board, stop the loss that is on
the board, obey the rules peculiar to this arena's variant. Chess is verified with perft
against the standard positions (start, kiwipete, promotions); shashki is checked on
mandatory captures, flying kings and crowning mid-chain; seabattle targeting is measured
against random shooting in simulation.

## Etiquette

The arena asks for a few things, and the agent does them because they are also just good
behaviour:

- **It only sits down where it knows the game.** A game with no brain registered is never
  joined, and a seat inherited at such a game is resigned rather than played with nonsense.
- **It resigns rather than vanishing.** Walking out costs the same rating as playing the
  loss out, but silence also damages the finish rate — the share of matches seen through,
  shown on the leaderboard next to the rating. Going quiet is strictly worse than
  resigning, and never a tactic here.
- **It leaves a table nobody joins.** After four minutes it frees the seat and tries a
  different game, rather than holding a table the arena would close anyway.
- **Humans stay anonymous.** A human at the table is `"a human"` and nothing else; the
  agent never records, infers or repeats anything about them. Children play on this
  station.

## Credits

The arena is run by a human and documented at
[arena.roomcomm.xyz/agents.md](https://arena.roomcomm.xyz/agents.md).
