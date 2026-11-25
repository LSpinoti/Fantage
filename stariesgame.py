"""Exact Python port of the Staries game logic (StariesScene-*.js, class `Os`).

Given a state snapshot from gamestate.read(), this predicts precisely what a
move does: the refills (same Mulberry32 RNG), bombs, blasts, score, level
progress, level clears and lock placement.

Board is column-major like the game: board[x][y], x = column, y = row (0 = top).
Star = [colour 0..7, locked bool, bomb bool].
A move is (axis, index, steps): axis 0 = row `index` shifted right by `steps`,
axis 1 = column `index` shifted down by `steps`.
"""
N = 6            # board size (c)
COLOURS = 8      # ($)
MIN_GROUP = 3    # (T)
BOMB_SIZE = 5    # (_s) groups this big leave a bomb behind
GROUP_BASE = 10  # (Ss)
GROUP_PER = 5    # (ks)
COMBO = 10       # (Es) per extra group in one break
CLEAR_BONUS = 1000            # (ts)
NEEDED_BASE, NEEDED_PER = 70, 20   # (xs, Ms) stars to break per level
LOCK_FROM = 3                 # (V) locks start at this level
LOCK_BASE_MS = 20000          # (K)
LOCK_FACTOR = 0.8             # (vs) per level above LOCK_FROM
T_INTRO, T_EXPLODE, T_FALL_ROW, T_LOCK, T_CLEAR, T_CLEAR_WAIT = 2320, 150, 60, 200, 3000, 1000
M32 = 0xFFFFFFFF


class Rng:
    """Mulberry32, identical to MinigameRng-*.js."""
    __slots__ = ('value',)

    def __init__(self, state):
        self.value = state & M32

    def next_u32(self):
        self.value = (self.value + 0x6D2B79F5) & M32
        t = self.value
        t = ((t ^ (t >> 15)) * (t | 1)) & M32
        t ^= (t + (((t ^ (t >> 7)) * (t | 61)) & M32)) & M32
        return (t ^ (t >> 14)) & M32

    def below(self, n):
        return (self.next_u32() * n) >> 32


def shift_line(board, axis, index, steps):
    """The game's `v`: cyclic shift of a row (axis 0) or column (axis 1)."""
    if axis == 0:
        line = [board[i][index] for i in range(N)]
        for i in range(N):
            board[i][index] = line[(i - steps) % N]
    else:
        line = [board[index][i] for i in range(N)]
        for i in range(N):
            board[index][i] = line[(i - steps) % N]


class Game:
    def __init__(self, snap=None):
        if snap is None:
            return
        self.board = [[[s[0], bool(s[1]), bool(s[2])] for s in col] for col in snap['board']]
        self.level = snap['level']
        self.score = snap['score']
        self.broken = snap['broken']
        self.needed = snap['needed']
        self.now = snap['now']
        self.last_lock = snap['lastLockTime']
        self.lock_interval = snap['lockInterval']
        self.rng = Rng(snap['rng'])
        self.phase = snap['phase']

    def copy(self):
        g = Game()
        g.board = [[s[:] for s in col] for col in self.board]
        g.level, g.score, g.broken, g.needed = self.level, self.score, self.broken, self.needed
        g.now, g.last_lock, g.lock_interval, g.phase = self.now, self.last_lock, self.lock_interval, self.phase
        g.rng = Rng(self.rng.value)
        return g

    # ------------------------------------------------------------ queries
    def can_slide(self, axis, index):
        for i in range(N):
            s = self.board[i][index] if axis == 0 else self.board[index][i]
            if s[1]:
                return False
        return True

    def group_at(self, x, y):
        b = self.board
        colour = b[x][y][0]
        cells = [(x, y)]
        seen = {x * N + y}
        i = 0
        while i < len(cells):
            cx, cy = cells[i]
            i += 1
            for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                if 0 <= nx < N and 0 <= ny < N and (nx * N + ny) not in seen and b[nx][ny][0] == colour:
                    seen.add(nx * N + ny)
                    cells.append((nx, ny))
        return cells

    def groups(self):
        out = []
        taken = set()
        for x in range(N):
            for y in range(N):
                if x * N + y in taken:
                    continue
                g = self.group_at(x, y)
                if len(g) >= MIN_GROUP:
                    out.append(g)
                    for cx, cy in g:
                        taken.add(cx * N + cy)
        return out

    def line_makes_group(self, axis, index):
        for i in range(N):
            x, y = (i, index) if axis == 0 else (index, i)
            if len(self.group_at(x, y)) >= MIN_GROUP:
                return True
        return False

    def slide_exists(self):
        for axis in (0, 1):
            for index in range(N):
                if self.can_slide(axis, index):
                    for k in range(1, N):
                        shift_line(self.board, axis, index, 1)
                        if self.line_makes_group(axis, index):
                            shift_line(self.board, axis, index, N - k)
                            return True
                    shift_line(self.board, axis, index, 1)
        return False

    def legal_moves(self):
        """All (axis, index, steps) the game would accept."""
        out = []
        for axis in (0, 1):
            for index in range(N):
                if not self.can_slide(axis, index):
                    continue
                for steps in range(1, N):
                    shift_line(self.board, axis, index, steps)
                    if self.line_makes_group(axis, index):
                        out.append((axis, index, steps))
                    shift_line(self.board, axis, index, N - steps)
        return out

    # ------------------------------------------------------------ dynamics
    def new_star(self):
        return [self.rng.below(COLOURS), False, False]

    def deal(self):
        while True:
            for x in range(N):
                for y in range(N):
                    self.board[x][y] = self.new_star()
            while True:
                gs = self.groups()
                if not gs:
                    break
                for g in gs:
                    x, y = g[self.rng.below(len(g))]
                    self.board[x][y][0] = self.rng.below(COLOURS)
            if self.slide_exists():
                return

    def start_level(self, level, t):
        self.level = level
        self.needed = NEEDED_BASE + (level - 1) * NEEDED_PER
        self.broken = 0
        self.lock_interval = LOCK_BASE_MS
        for _ in range(LOCK_FROM, level):
            self.lock_interval *= LOCK_FACTOR
        self.deal()
        self.now = t + T_INTRO            # intro ends -> play; lock clock restarts
        self.last_lock = self.now
        self.phase = 'play'

    def blown_by(self, groups, in_groups):
        blown = []
        seen = set()

        def blast(x, y):
            for xx in range(x - 1, x + 2):
                if not 0 <= xx < N:
                    continue
                for yy in range(y - 1, y + 2):
                    if not 0 <= yy < N:
                        continue
                    k = xx * N + yy
                    if k not in in_groups and k not in seen:
                        seen.add(k)
                        blown.append((xx, yy))
        for g in groups:
            for x, y in g:
                if self.board[x][y][2]:
                    blast(x, y)
        i = 0
        while i < len(blown):
            x, y = blown[i]
            i += 1
            if self.board[x][y][2]:
                blast(x, y)
        return blown

    def break_groups(self, groups, stats):
        in_groups = {x * N + y for g in groups for x, y in g}
        blown = self.blown_by(groups, in_groups)
        combo = (len(groups) - 1) * COMBO if len(groups) > 1 else 0
        self.score += combo
        stats['points'] += combo
        new_bombs = set()
        for g in groups:
            pts = GROUP_BASE + (len(g) - MIN_GROUP) * GROUP_PER
            self.score += pts
            stats['points'] += pts
            if len(g) >= BOMB_SIZE:
                x, y = g[self.rng.below(len(g))]
                s = self.board[x][y]
                s[2] = True
                s[1] = False
                new_bombs.add(x * N + y)
                stats['bombs_made'] += 1
        gone = set()
        for x, y in blown:
            gone.add(x * N + y)
        for g in groups:
            for x, y in g:
                k = x * N + y
                if k not in new_bombs:
                    gone.add(k)
        stats['locks_cleared'] += sum(self.board[k // N][k % N][1] for k in gone)
        stats['bombs_used'] += sum(self.board[k // N][k % N][2] for k in gone)
        self.broken += len(gone)
        stats['broken'] += len(gone)
        stats['steps'] += 1
        self.now += T_EXPLODE
        # fall
        max_fall = 0
        for x in range(N):
            col = self.board[x]
            cnt = 0
            for y in range(N - 1, -1, -1):
                if x * N + y in gone:
                    cnt += 1
                elif cnt > 0:
                    col[y + cnt] = col[y]
            for i in range(cnt):
                col[cnt - i - 1] = self.new_star()
            max_fall = max(max_fall, cnt)
        self.now += max_fall * T_FALL_ROW

    def slide(self, move, t=None):
        """Apply a move made at game time t (default: now). Returns a stats
        dict, or None if the game would reject the move. Resolves everything
        up to the next 'play' (or 'over') phase, including level clears."""
        axis, index, steps = move
        if self.phase != 'play' or not self.can_slide(axis, index):
            return None
        shift_line(self.board, axis, index, steps)
        gs = self.groups()
        if not gs:
            shift_line(self.board, axis, index, N - steps)
            return None
        if t is not None:
            self.now = t
        stats = dict(points=0, broken=0, steps=0, bombs_made=0, bombs_used=0, locks_cleared=0,
                     locks_added=0, level_up=False, start=self.now)
        while gs:
            self.break_groups(gs, stats)
            gs = self.groups()
        if self.broken >= self.needed:
            self.now += T_CLEAR
            self.score += CLEAR_BONUS
            stats['points'] += CLEAR_BONUS
            self.now += T_CLEAR_WAIT
            stats['level_up'] = True
            self.start_level(self.level + 1, self.now)
        else:
            due = 0
            if self.level >= LOCK_FROM:
                due = int((self.now - self.last_lock) // self.lock_interval)
                if due > 0:
                    self.last_lock = self.now
            if due > 0:
                free = sum(not s[1] for col in self.board for s in col)
                cells = []
                for _ in range(min(due, free)):
                    while True:
                        x, y = self.rng.below(N), self.rng.below(N)
                        if not self.board[x][y][1]:
                            break
                    cells.append((x, y))
                self.now += T_LOCK
                for x, y in cells:
                    self.board[x][y][1] = True
                stats['locks_added'] = len(cells)
            self.phase = 'play' if self.slide_exists() else 'over'
        stats['end'] = self.now
        return stats

    def signature(self):
        return tuple(tuple(tuple(s) for s in col) for col in self.board)
