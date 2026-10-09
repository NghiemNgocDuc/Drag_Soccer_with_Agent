import uuid, os
os.environ["DEV_MODE"] = "1"
import app as appmod
from db.clans import _MEM_CLANS, _MEM_MEMBERS, _MEM_REQUESTS, _MEM_USER_CLAN, _MEM_MEMBER_SINCE
from db.profiles import _MEM_USERS

def clear():
    _MEM_CLANS.clear(); _MEM_MEMBERS.clear(); _MEM_REQUESTS.clear(); _MEM_USER_CLAN.clear(); _MEM_MEMBER_SINCE.clear()
    _MEM_USERS.clear()

def test_open_vs_request_join():
    clear()
    with appmod.app.test_client() as c:
        c.post("/auth/register", json={"email":"c_leader@test.com","password":"123456","confirm":"123456","username":"c_leader"})
        r=c.post("/api/clans", json={"name":"ReqClan","join_type":"request"})
        assert r.status_code==201
        cid=r.get_json()["clan"]["id"]
        c.get("/auth/logout")
        c.post("/auth/register", json={"email":"m1@test.com","password":"123456","confirm":"123456","username":"m1"})
        r=c.post(f"/api/clans/{cid}/join", json={})
        assert r.get_json()["joined"] is False  # request mode needs approval
        # leader approves
        c.get("/auth/logout")
        c.post("/auth/login", json={"email":"c_leader@test.com","password":"123456"})
        r=c.get(f"/api/clans/{cid}")
        req_id=r.get_json()["pending"][0]["id"]
        r=c.post(f"/api/clans/{cid}/requests/{req_id}/approve")
        assert r.status_code==200
        # m1 now member, one-clan guard
        c.get("/auth/logout")
        c.post("/auth/login", json={"email":"m1@test.com","password":"123456"})
        r=c.post("/api/clans", json={"name":"ShouldFail","join_type":"open"})
        assert r.status_code==400

def test_open_auto_join():
    clear()
    with appmod.app.test_client() as c:
        c.post("/auth/register", json={"email":"open_leader@test.com","password":"123456","confirm":"123456","username":"open_leader"})
        r=c.post("/api/clans", json={"name":"OpenC","join_type":"open"})
        cid=r.get_json()["clan"]["id"]
        c.get("/auth/logout")
        c.post("/auth/register", json={"email":"joiner@test.com","password":"123456","confirm":"123456","username":"joiner"})
        r=c.post(f"/api/clans/{cid}/join", json={})
        assert r.get_json()["joined"] is True
        r=c.get(f"/api/clans/{cid}")
        assert len(r.get_json()["clan"]["members"])==2

def test_leader_transfer_loses_role():
    clear()
    with appmod.app.test_client() as c:
        c.post("/auth/register", json={"email":"l1@test.com","password":"123456","confirm":"123456","username":"l1"})
        r=c.post("/api/clans", json={"name":"XX","join_type":"open"})
        cid=r.get_json()["clan"]["id"]
        c.get("/auth/logout")
        c.post("/auth/register", json={"email":"m2@test.com","password":"123456","confirm":"123456","username":"m2"})
        c.post(f"/api/clans/{cid}/join", json={})
        c.get("/auth/logout")
        c.post("/auth/login", json={"email":"l1@test.com","password":"123456"})
        # get m2 uid
        from db.clans import list_members
        mid = [m["user_id"] for m in list_members(cid) if m["username"]=="m2"][0]
        r=c.post(f"/api/clans/{cid}/transfer", json={"new_leader_id": mid})
        assert r.status_code==200
        assert r.get_json()["clan"]["leader_id"]==mid
        # old leader still member but not leader
        r=c.get(f"/api/clans/{cid}")
        assert r.get_json()["clan"]["leader_id"]!= "dev:l1@test.com"
        # old leader cannot transfer again
        r=c.post(f"/api/clans/{cid}/transfer", json={"new_leader_id": mid})
        assert r.status_code==400
        # new leader cannot leave (leader cannot leave)
        c.get("/auth/logout")
        c.post("/auth/login", json={"email":"m2@test.com","password":"123456"})
        r=c.post(f"/api/clans/{cid}/leave")
        assert r.status_code==400
        # old leader can leave
        c.get("/auth/logout")
        c.post("/auth/login", json={"email":"l1@test.com","password":"123456"})
        r=c.post(f"/api/clans/{cid}/leave")
        assert r.status_code==200

def test_clan_pages_render():
    clear()
    with appmod.app.test_client() as c:
        c.post("/auth/register", json={"email":"p@test.com","password":"123456","confirm":"123456","username":"pp"})
        r=c.get("/clans")
        assert r.status_code==200 and b"Create clan" in r.data
        r=c.post("/api/clans", json={"name":"PageClan","join_type":"open"})
        cid=r.get_json()["clan"]["id"]
        r=c.get(f"/clans/{cid}")
        assert r.status_code==200 and b"LEADER" in r.data
