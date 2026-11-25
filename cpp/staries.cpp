// Exact C++ port of the Staries game logic (same as stariesgame.py) plus the
// beam-search planner (same as planner.py), exposed through a C API for ctypes.
//
// Build:  g++ -O3 -march=native -shared -fPIC -o libstaries.so staries.cpp
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <vector>

namespace {

constexpr int N = 6;
constexpr int COLOURS = 8, MIN_GROUP = 3, BOMB_SIZE = 5;
constexpr int GROUP_BASE = 10, GROUP_PER = 5, COMBO = 10, CLEAR_BONUS = 1000;
constexpr int NEEDED_BASE = 70, NEEDED_PER = 20, LOCK_FROM = 3;
constexpr double LOCK_BASE_MS = 20000.0, LOCK_FACTOR = 0.8;
constexpr double T_INTRO = 2320, T_EXPLODE = 150, T_FALL_ROW = 60, T_LOCK = 200, T_CLEAR = 3000,
                 T_CLEAR_WAIT = 1000;

enum Phase : uint8_t { PLAY = 0, OVER = 1 };

struct Rng {
    uint32_t v;
    uint32_t next() {
        v += 0x6D2B79F5u;
        uint32_t t = v;
        t = (t ^ (t >> 15)) * (t | 1u);
        t ^= t + (t ^ (t >> 7)) * (t | 61u);
        return t ^ (t >> 14);
    }
    int below(int n) { return int((uint64_t(next()) * uint64_t(n)) >> 32); }
};

struct Star {
    uint8_t colour, locked, bomb;
};

struct Stats {
    int points = 0, broken = 0, steps = 0, locks_added = 0;
    bool level_up = false;
    double end = 0;
};

struct Game {
    Star b[N][N];  // b[x][y], y = 0 top
    int level, score, broken, needed;
    double now, last_lock, lock_interval;
    Rng rng;
    Phase phase;

    void shift(int axis, int index, int steps) {
        Star line[N];
        for (int i = 0; i < N; i++) line[i] = axis == 0 ? b[i][index] : b[index][i];
        for (int i = 0; i < N; i++) {
            Star s = line[((i - steps) % N + N) % N];
            if (axis == 0) b[i][index] = s; else b[index][i] = s;
        }
    }
    bool can_slide(int axis, int index) const {
        for (int i = 0; i < N; i++)
            if ((axis == 0 ? b[i][index] : b[index][i]).locked) return false;
        return true;
    }
    // BFS group from (x,y) in the game's neighbour order; returns size, cells in out
    int group_at(int x, int y, int* out) const {
        uint64_t seen = 1ull << (x * N + y);
        int n = 0;
        out[n++] = x * N + y;
        int c = b[x][y].colour;
        for (int i = 0; i < n; i++) {
            int cx = out[i] / N, cy = out[i] % N;
            const int nb[4][2] = {{cx - 1, cy}, {cx + 1, cy}, {cx, cy - 1}, {cx, cy + 1}};
            for (auto& p : nb) {
                int nx = p[0], ny = p[1];
                if (nx < 0 || nx >= N || ny < 0 || ny >= N) continue;
                int k = nx * N + ny;
                if ((seen >> k) & 1) continue;
                if (b[nx][ny].colour != c) continue;
                seen |= 1ull << k;
                out[n++] = k;
            }
        }
        return n;
    }
    // groups: flattened cells + offsets
    int groups(int* cells, int* start, int* size) const {
        uint64_t taken = 0;
        int ng = 0, nc = 0;
        int tmp[N * N];
        for (int x = 0; x < N; x++)
            for (int y = 0; y < N; y++) {
                if ((taken >> (x * N + y)) & 1) continue;
                int n = group_at(x, y, tmp);
                if (n >= MIN_GROUP) {
                    start[ng] = nc;
                    size[ng] = n;
                    for (int i = 0; i < n; i++) {
                        cells[nc++] = tmp[i];
                        taken |= 1ull << tmp[i];
                    }
                    ng++;
                }
            }
        return ng;
    }
    bool line_makes_group(int axis, int index) const {
        int tmp[N * N];
        for (int i = 0; i < N; i++) {
            int x = axis == 0 ? i : index, y = axis == 0 ? index : i;
            if (group_at(x, y, tmp) >= MIN_GROUP) return true;
        }
        return false;
    }
    bool slide_exists() {
        for (int axis = 0; axis < 2; axis++)
            for (int index = 0; index < N; index++)
                if (can_slide(axis, index)) {
                    for (int k = 1; k < N; k++) {
                        shift(axis, index, 1);
                        if (line_makes_group(axis, index)) {
                            shift(axis, index, N - k);
                            return true;
                        }
                    }
                    shift(axis, index, 1);
                }
        return false;
    }
    int legal_moves(int (*out)[3]) {
        int n = 0;
        for (int axis = 0; axis < 2; axis++)
            for (int index = 0; index < N; index++) {
                if (!can_slide(axis, index)) continue;
                for (int steps = 1; steps < N; steps++) {
                    shift(axis, index, steps);
                    if (line_makes_group(axis, index)) {
                        out[n][0] = axis; out[n][1] = index; out[n][2] = steps;
                        n++;
                    }
                    shift(axis, index, N - steps);
                }
            }
        return n;
    }
    Star new_star() { return Star{uint8_t(rng.below(COLOURS)), 0, 0}; }
    void deal() {
        int cells[N * N], st[N * N], sz[N * N];
        for (;;) {
            for (int x = 0; x < N; x++)
                for (int y = 0; y < N; y++) b[x][y] = new_star();
            for (;;) {
                int ng = groups(cells, st, sz);
                if (!ng) break;
                for (int g = 0; g < ng; g++) {
                    int k = cells[st[g] + rng.below(sz[g])];
                    b[k / N][k % N].colour = uint8_t(rng.below(COLOURS));
                }
            }
            if (slide_exists()) return;
        }
    }
    void start_level(int lv, double t) {
        level = lv;
        needed = NEEDED_BASE + (lv - 1) * NEEDED_PER;
        broken = 0;
        lock_interval = LOCK_BASE_MS;
        for (int i = LOCK_FROM; i < lv; i++) lock_interval *= LOCK_FACTOR;
        deal();
        now = t + T_INTRO;
        last_lock = now;
        phase = PLAY;
    }
    void break_groups(int ng, const int* cells, const int* st, const int* sz, Stats& s) {
        uint64_t in_groups = 0;
        for (int i = 0; i < st[ng - 1] + sz[ng - 1]; i++) in_groups |= 1ull << cells[i];
        // blown cells (bombs in groups blast 3x3, chained)
        int blown[N * N], nb = 0;
        uint64_t seen = 0;
        auto blast = [&](int x, int y) {
            for (int xx = x - 1; xx <= x + 1; xx++) {
                if (xx < 0 || xx >= N) continue;
                for (int yy = y - 1; yy <= y + 1; yy++) {
                    if (yy < 0 || yy >= N) continue;
                    int k = xx * N + yy;
                    if (((in_groups | seen) >> k) & 1) continue;
                    seen |= 1ull << k;
                    blown[nb++] = k;
                }
            }
        };
        for (int g = 0; g < ng; g++)
            for (int i = 0; i < sz[g]; i++) {
                int k = cells[st[g] + i];
                if (b[k / N][k % N].bomb) blast(k / N, k % N);
            }
        for (int i = 0; i < nb; i++) {
            int k = blown[i];
            if (b[k / N][k % N].bomb) blast(k / N, k % N);
        }
        int combo = ng > 1 ? (ng - 1) * COMBO : 0;
        score += combo;
        s.points += combo;
        uint64_t new_bombs = 0;
        for (int g = 0; g < ng; g++) {
            int pts = GROUP_BASE + (sz[g] - MIN_GROUP) * GROUP_PER;
            score += pts;
            s.points += pts;
            if (sz[g] >= BOMB_SIZE) {
                int k = cells[st[g] + rng.below(sz[g])];
                Star& star = b[k / N][k % N];
                star.bomb = 1;
                star.locked = 0;
                new_bombs |= 1ull << k;
            }
        }
        uint64_t gone = seen | (in_groups & ~new_bombs);
        int cnt = __builtin_popcountll(gone);
        broken += cnt;
        s.broken += cnt;
        s.steps++;
        now += T_EXPLODE;
        int max_fall = 0;
        for (int x = 0; x < N; x++) {
            int c = 0;
            for (int y = N - 1; y >= 0; y--) {
                if ((gone >> (x * N + y)) & 1) c++;
                else if (c > 0) b[x][y + c] = b[x][y];
            }
            for (int i = 0; i < c; i++) b[x][c - i - 1] = new_star();
            max_fall = std::max(max_fall, c);
        }
        now += max_fall * T_FALL_ROW;
    }
    // returns false if the move is rejected
    bool slide(int axis, int index, int steps, double t, Stats& s) {
        if (phase != PLAY || !can_slide(axis, index)) return false;
        shift(axis, index, steps);
        int cells[N * N], st[N * N], sz[N * N];
        int ng = groups(cells, st, sz);
        if (!ng) {
            shift(axis, index, N - steps);
            return false;
        }
        now = t;
        while (ng) {
            break_groups(ng, cells, st, sz, s);
            ng = groups(cells, st, sz);
        }
        if (broken >= needed) {
            now += T_CLEAR;
            score += CLEAR_BONUS;
            s.points += CLEAR_BONUS;
            now += T_CLEAR_WAIT;
            s.level_up = true;
            start_level(level + 1, now);
        } else {
            int due = 0;
            if (level >= LOCK_FROM) {
                due = int(std::floor((now - last_lock) / lock_interval));
                if (due > 0) last_lock = now;
            }
            if (due > 0) {
                int free_ = 0;
                for (int x = 0; x < N; x++)
                    for (int y = 0; y < N; y++) free_ += !b[x][y].locked;
                int n = std::min(due, free_);
                int lx[N * N], ly[N * N];
                for (int i = 0; i < n; i++) {
                    int x, y;
                    do { x = rng.below(N); y = rng.below(N); } while (b[x][y].locked);
                    lx[i] = x; ly[i] = y;
                    // the game only sets `locked` after picking all cells; picking checks
                    // the board as it was, so mark after the loop (see below)
                }
                now += T_LOCK;
                for (int i = 0; i < n; i++) b[lx[i]][ly[i]].locked = 1;
                s.locks_added = n;
            }
            phase = slide_exists() ? PLAY : OVER;
        }
        s.end = now;
        return true;
    }
    int free_lines() const {
        int n = 0;
        for (int a = 0; a < 2; a++)
            for (int i = 0; i < N; i++) n += can_slide(a, i);
        return n;
    }
};

struct Node {
    double total, acc;
    int first;  // index into first-move table
    Game g;
    double t;
};

}  // namespace

extern "C" {

// board: 36*3 ints, index (x*6+y)*3 + {colour, locked, bomb}
// weights: [W_BROKEN, W_FREE, W_OVER, DISCOUNT]
// Returns number of distinct first moves considered (0 = no legal move).
int staries_plan(const int* board, int level, int score, int broken, int needed, double now,
                 double last_lock, double lock_interval, uint32_t rng_state, double t0, int depth,
                 int beam, double react_ms, const double* weights, int* out_move, double* out_value) {
    Game root;
    for (int x = 0; x < N; x++)
        for (int y = 0; y < N; y++) {
            const int* s = board + (x * N + y) * 3;
            root.b[x][y] = Star{uint8_t(s[0]), uint8_t(s[1] != 0), uint8_t(s[2] != 0)};
        }
    root.level = level; root.score = score; root.broken = broken; root.needed = needed;
    root.now = now; root.last_lock = last_lock; root.lock_interval = lock_interval;
    root.rng.v = rng_state; root.phase = PLAY;
    const double W_BROKEN = weights[0], W_FREE = weights[1], W_OVER = weights[2], DISC = weights[3];

    std::vector<int> firsts;  // encoded first moves in insertion order
    std::vector<double> best_first;
    auto first_index = [&](int code) {
        for (size_t i = 0; i < firsts.size(); i++)
            if (firsts[i] == code) return int(i);
        firsts.push_back(code);
        best_first.push_back(-1e300);
        return int(firsts.size() - 1);
    };

    std::vector<Node> frontier;
    frontier.push_back(Node{0, 0, -1, root, t0});
    int moves[64][3];
    for (int d = 0; d < depth; d++) {
        double w = std::pow(DISC, d);
        std::vector<Node> cands;
        for (auto& nd : frontier) {
            if (nd.g.phase != PLAY) continue;
            int nm = nd.g.legal_moves(moves);
            for (int m = 0; m < nm; m++) {
                Node c{0, 0, nd.first, nd.g, 0};
                Stats st;
                if (!c.g.slide(moves[m][0], moves[m][1], moves[m][2], nd.t, st)) continue;
                c.acc = nd.acc + w * (st.points + W_BROKEN * st.broken);
                if (c.first < 0) c.first = first_index(moves[m][0] * 64 + moves[m][1] * 8 + moves[m][2]);
                c.t = st.end + react_ms;
                double leaf = c.g.phase == OVER ? -W_OVER : W_FREE * c.g.free_lines();
                c.total = c.acc + leaf;
                cands.push_back(c);
            }
        }
        if (cands.empty()) break;
        std::stable_sort(cands.begin(), cands.end(),
                         [](const Node& a, const Node& b) { return a.total > b.total; });
        for (auto& c : cands)
            if (c.total > best_first[c.first]) best_first[c.first] = c.total;
        if ((int)cands.size() > beam) cands.resize(beam);
        frontier.swap(cands);
    }
    if (firsts.empty()) return 0;
    int bi = 0;
    for (size_t i = 1; i < firsts.size(); i++)
        if (best_first[i] > best_first[bi]) bi = int(i);
    out_move[0] = firsts[bi] / 64;
    out_move[1] = (firsts[bi] / 8) % 8;
    out_move[2] = firsts[bi] % 8;
    *out_value = best_first[bi];
    return int(firsts.size());
}

}  // extern "C"
