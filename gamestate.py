"""Read-only access to the live Staries game state in the headless browser.

The game logic lives in StariesScene-*.js (class with board / phase / rng ...).
When the bot's browser loads that file, we add one statement to its
constructor, `globalThis.__staries = this`, so page.evaluate() can read the
state. Nothing else in the game or its server traffic is changed; the server
still replays and validates every input.
"""
import re

_PATCH = re.compile(r"(this\['rng'\]=new _0x[0-9a-f]+\(_0x[0-9a-f]+\))")
# score at which this player's gem is awarded (from the server's data)
_GEM_PATCH = re.compile(r"'gemScore':(_0x[0-9a-f]+\(this\['shard'\],_0x[0-9a-f]+\['id'\]\))")


def install(context):
    """Register the route that exposes the game object. Call before navigating."""
    def handle(route):
        resp = route.fetch()
        body = resp.text()
        patched, n = _PATCH.subn(r"\1,globalThis.__staries=this", body, count=1)
        patched, _ = _GEM_PATCH.subn(r"'gemScore':(globalThis.__gemScore=\1)", patched, count=1)
        route.fulfill(response=resp, body=patched if n else body,
                      headers={**resp.headers, 'cache-control': 'no-store'})
    context.route(re.compile(r'.*/assets/StariesScene-[^/]*\.js$'), handle)


_READ = """() => {
  const g = globalThis.__staries;
  if (!g) return null;
  return {
    phase: g.phase, level: g.level, score: g.score, broken: g.broken, needed: g.needed,
    now: g.now, phaseEnd: g.phaseEnd, lastLockTime: g.lastLockTime, lockInterval: g.lockInterval,
    ended: g.ended, rng: g.rng.state,
    gemScore: globalThis.__gemScore === undefined || globalThis.__gemScore === null ? null : Number(globalThis.__gemScore),
    board: g.board.map(col => col.map(s => [s.colour, s.locked ? 1 : 0, s.bomb ? 1 : 0])),
  };
}"""


def read(page):
    """Snapshot of the game logic (None if no game object yet).
    board[x][y] (column-major, y=0 top): [colour 0-7, locked, bomb]."""
    return page.evaluate(_READ)
