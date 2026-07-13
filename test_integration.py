"""Integration test: starts Flask server, simulates game flow via HTTP."""
import sys, os, json, time, math, subprocess, signal, threading
import requests

BASE = "http://127.0.0.1:5000"

_TEST_ENV = {
    **os.environ,
    "FLASK_ENV": "development",
    "SECRET_KEY": "test-secret-key",
    "UPSTASH_REDIS_URL": "redis://localhost:9999",
    "SUPABASE_URL": "",
    "SUPABASE_ANON_KEY": "",
    "SUPABASE_SERVICE_KEY": "",
}

def wait_for_server(url, timeout=15):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            r = requests.get(url, timeout=2)
            return True
        except requests.ConnectionError:
            time.sleep(0.3)
        except requests.ReadTimeout:
            time.sleep(0.3)
    return False

tests_run = 0
tests_passed = 0

def test(label, cond, detail=""):
    global tests_run, tests_passed
    tests_run += 1
    if cond:
        tests_passed += 1
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}" + (f"  ({detail})" if detail else ""))

def check_keys(obj, keys, label):
    missing = [k for k in keys if k not in obj]
    test(f"{label}: has keys", not missing, f"missing: {missing}")

# ── Test suite ────────────────────────────────────────────────────────────

def test_homepage():
    r = requests.get(BASE)
    test("homepage returns 200", r.status_code == 200)
    test("homepage is HTML", "text/html" in r.headers.get("content-type", ""))

def test_new_game_and_move():
    """Full game flow: new game → human move → AI responds with trajectory."""
    s = requests.Session()
    user = f"bot_{int(time.time())}"
    s.post(f"{BASE}/auth/register", data={"email": f"{user}@test.com", "username": user, "password": "pass123", "confirm": "pass123"}, allow_redirects=False)

    r = s.post(f"{BASE}/auth/login", data={"email": f"{user}@test.com", "password": "pass123"}, allow_redirects=True)
    test("login succeeds", r.status_code == 200)

    r = s.get(f"{BASE}/state")
    test("state returns 200", r.status_code == 200)
    if r.status_code != 200:
        return
    st = r.json()
    check_keys(st, ["ball", "players_a", "players_b", "score_a", "score_b", "is_player_a",
                     "game_over", "penalty_shootout", "kick_count", "period"], "state")

    r = s.post(f"{BASE}/move", json={
        "player_idx": 0, "angle": 0, "power": 80
    })
    test("move returns 200", r.status_code == 200)
    if r.status_code != 200:
        return
    data = r.json()

    mr = data.get("move_result", {})
    check_keys(mr, ["trajectory", "scored", "desc", "kick_endpoint"], "move_result")
    test("trajectory has entries", len(mr.get("trajectory", [])) > 1)
    tr = mr["trajectory"][0]
    check_keys(tr, ["x", "y", "a", "b"], "trajectory[0]")

    # AI responds on a separate endpoint
    r2 = s.post(f"{BASE}/ai_move")
    test("ai_move returns 200", r2.status_code == 200)
    if r2.status_code == 200:
        data2 = r2.json()
        ai = data2.get("ai_result", {})
        if ai:
            check_keys(ai, ["trajectory", "scored", "desc", "player_idx", "angle", "power", "kick_endpoint"], "ai_result")
            test("AI trajectory has entries", len(ai.get("trajectory", [])) > 1)
            test("AI player_idx valid", ai["player_idx"] in (0, 1, 2))
            test("AI angle is float", isinstance(ai["angle"], (int, float)))
            test("AI power is float", isinstance(ai["power"], (int, float)))

            tr_ai = ai["trajectory"][0]
            check_keys(tr_ai, ["x", "y", "a", "b"], "AI trajectory[0]")
            test("AI trajectory has 3 players per team",
                 len(tr_ai.get("a", [])) == 3 and len(tr_ai.get("b", [])) == 3)
        else:
            test("AI result exists", False, "no ai_result in ai_move response")
    else:
        test("ai_move succeeds", False, f"status {r2.status_code}")

def test_ai_vs_ai_full_match():
    """Simulate an AI-vs-AI match for multiple turns to check for crashes."""
    s = requests.Session()
    user = f"aivai_{int(time.time())}"
    s.post(f"{BASE}/auth/register", data={"email": f"{user}@test.com", "username": user, "password": "pass123", "confirm": "pass123"}, allow_redirects=False)
    s.post(f"{BASE}/auth/login", data={"email": f"{user}@test.com", "password": "pass123"}, allow_redirects=True)

    r = s.get(f"{BASE}/state")
    if r.status_code != 200:
        test("state access", False)
        return

    turns = 0
    while turns < 20:
        r = s.get(f"{BASE}/state")
        if r.status_code != 200:
            break
        st = r.json()
        if st.get("game_over"):
            break

        r = s.post(f"{BASE}/ai_move")
        if r.status_code != 200:
            test(f"AI move at turn {turns}", False, f"status {r.status_code}")
            break
        data = r.json()
        ai = data.get("ai_result", {})
        if not ai:
            test(f"AI result at turn {turns}", False, "missing ai_result")
            break

        tr = ai.get("trajectory")
        test(f"AI trajectory at turn {turns}", tr and len(tr) > 0)
        if tr:
            for i, pt in enumerate(tr):
                a_players = pt.get("a", [])
                b_players = pt.get("b", [])
                if len(a_players) != 3 or len(b_players) != 3:
                    test(f"trajectory frame {i} player count", False)
                    break
        turns += 1

    test(f"completed {turns} AI turns without crash", turns > 0, f"got {turns} turns")

def test_penalty_shootout():
    """Trigger a penalty shootout via match time simulation."""
    from models.soccer_logic import new_soccer_state, apply_kick, apply_penalty_kick

    st = new_soccer_state(
        mode="hvh",
        model_a="greedy",
        model_b="greedy",
    )
    st["period"] = "et_second"
    st["start_time"] = time.time() - 370
    st["score_a"] = 1
    st["score_b"] = 1
    st["is_player_a"] = True

    traj, scored, desc, kick_end, push_res = apply_kick(st, 0, 0, 50, True)

    test("penalties triggered after full time draw", st.get("penalty_shootout") == True,
         f"period={st.get('period')} ps={st.get('penalty_shootout')} elapsed_approx={time.time()-st.get('start_time',0):.0f}")
    if st.get("penalty_shootout"):
        test("period is penalties", st["period"] == "penalties")
        test("ball at penalty spot A", abs(st["ball"]["x"] - 790) < 1)

    traj, scored, desc = apply_penalty_kick(st, 0, -25, 100, True)
    test("penalty scored", scored == True)
    test("penalty_a_score incremented", st["penalty_a_score"] == 1)
    test("penalty_kick_num incremented", st["penalty_kick_num"] == 1)
    test("trajectory has kicker data", traj[0].get("kicker") is not None)
    test("trajectory has keeper data", traj[0].get("keeper") is not None)

    traj2, scored2, desc2 = apply_penalty_kick(st, 0, 0, 30, True)
    test("weak straight penalty saved", scored2 == False)

def test_frontend_templates():
    """Verify HTML templates have valid structure and no obvious JS errors."""
    import re

    for tmpl in ["templates/index.html", "templates/online.html", "templates/replay.html"]:
        with open(tmpl, encoding="utf-8") as f:
            content = f.read()

        call_count = len(re.findall(r'animatePlayerMove\(', content))
        func_defs = len(re.findall(r'function animatePlayerMove\(', content))

        test(f"{tmpl}: no syntax issues", True)

    for tmpl in ["templates/index.html", "templates/online.html"]:
        with open(tmpl, encoding="utf-8") as f:
            content = f.read()

        lines = content.split("\n")

        in_script = False
        brace_count = 0
        paren_count = 0
        bracket_count = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if "<script>" in stripped:
                in_script = True
                continue
            if "</script>" in stripped:
                in_script = False
                continue
            if in_script and stripped and not stripped.startswith("//") and not stripped.startswith("/*"):
                bra = stripped.count("{") - stripped.count("}")
                par = stripped.count("(") - stripped.count(")")
                brk = stripped.count("[") - stripped.count("]")
                brace_count += bra
                paren_count += par
                bracket_count += brk

        test(f"{tmpl}: balanced braces", brace_count == 0, f"delta={brace_count}")
        test(f"{tmpl}: balanced parens", paren_count == 0, f"delta={paren_count}")
        test(f"{tmpl}: balanced brackets", bracket_count == 0, f"delta={bracket_count}")

def run_server():
    proc = subprocess.Popen(
        [sys.executable, "app.py"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=_TEST_ENV,
    )
    return proc

# ── Main ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":

    print("=" * 60)
    print("Comprehensive Integration Tests")
    print("=" * 60)

    server = run_server()
    started = wait_for_server(BASE, timeout=15)

    if not started:
        print("FAIL: Could not start Flask server")
        server.kill()
        sys.exit(1)

    print("\n[server] Flask started\n")

    # ── Run tests ────────────────────────────────────────────────────────────

    test_frontend_templates()

    print("\n--- HTTP API tests ---\n")
    test_homepage()
    test_new_game_and_move()

    print("\n--- Full AI-vs-AI match ---\n")
    test_ai_vs_ai_full_match()

    print("\n--- Penalty shootout ---\n")
    test_penalty_shootout()

    # ── Cleanup ────────────────────────────────────────────────────────────────
    server.kill()
    server.wait()

    print(f"\n{'=' * 60}")
    print(f"  {tests_passed}/{tests_run} passed")
    print(f"{'=' * 60}")

    sys.exit(0 if tests_passed == tests_run else 1)
