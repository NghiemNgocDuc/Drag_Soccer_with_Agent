"""CRUD for saved research papers in Supabase."""
from __future__ import annotations
from datetime import datetime, timezone

def _svc():
    from db.supabase_client import service
    return service


def get_saved_papers(user_id: str) -> list[dict]:
    svc = _svc()
    if not svc:
        return _dev_get_papers(user_id)
    try:
        rows = (
            svc.table("research_papers")
            .select("*")
            .eq("user_id", user_id)
            .order("saved_at", desc=True)
            .execute()
        )
        return rows.data or []
    except Exception:
        return _dev_get_papers(user_id)


def save_paper(user_id: str, paper: dict) -> dict | None:
    svc = _svc()
    if not svc:
        return _dev_save_paper(user_id, paper)
    try:
        existing = (
            svc.table("research_papers")
            .select("id")
            .eq("user_id", user_id)
            .eq("paper_id", paper.get("paperId", ""))
            .maybe_single()
            .execute()
        )
        if existing and existing.data:
            return existing.data
        row = {
            "user_id": user_id,
            "paper_id": paper.get("paperId", ""),
            "title": paper.get("title", "Untitled"),
            "authors": paper.get("authors", []),
            "year": paper.get("year"),
            "venue": paper.get("venue", ""),
            "abstract": paper.get("abstract", ""),
            "url": paper.get("url", ""),
            "citation_count": paper.get("citationCount", 0),
            "external_ids": paper.get("externalIds", {}),
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "notes": "",
            "tags": [],
        }
        result = svc.table("research_papers").insert(row).execute()
        return (result.data or [None])[0]
    except Exception:
        return _dev_save_paper(user_id, paper)


def delete_paper(user_id: str, paper_id: str) -> bool:
    svc = _svc()
    if not svc:
        return _dev_delete_paper(user_id, paper_id)
    try:
        svc.table("research_papers").delete().eq("user_id", user_id).eq("paper_id", paper_id).execute()
        return True
    except Exception:
        return _dev_delete_paper(user_id, paper_id)


def update_paper_notes(user_id: str, paper_id: str, notes: str, tags: list[str] | None = None) -> bool:
    svc = _svc()
    if not svc:
        return _dev_update_notes(user_id, paper_id, notes, tags)
    try:
        update = {"notes": notes}
        if tags is not None:
            update["tags"] = tags
        svc.table("research_papers").update(update).eq("user_id", user_id).eq("paper_id", paper_id).execute()
        return True
    except Exception:
        return _dev_update_notes(user_id, paper_id, notes, tags)


#  In-memory dev fallback 

_DEV_STORE: dict[str, list[dict]] = {}

def _dev_key(uid: str) -> str:
    return f"papers:{uid}"

def _dev_get_papers(uid: str) -> list[dict]:
    return _DEV_STORE.get(_dev_key(uid), [])

def _dev_save_paper(uid: str, paper: dict) -> dict:
    papers = _dev_get_papers(uid)
    for p in papers:
        if p["paper_id"] == paper.get("paperId", ""):
            return p
    entry = {
        "id": paper.get("paperId", ""),
        "paper_id": paper.get("paperId", ""),
        "title": paper.get("title", "Untitled"),
        "authors": paper.get("authors", []),
        "year": paper.get("year"),
        "venue": paper.get("venue", ""),
        "abstract": paper.get("abstract", ""),
        "url": paper.get("url", ""),
        "citation_count": paper.get("citationCount", 0),
        "external_ids": paper.get("externalIds", {}),
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "notes": "",
        "tags": [],
    }
    papers.append(entry)
    _DEV_STORE[_dev_key(uid)] = papers
    return entry

def _dev_delete_paper(uid: str, paper_id: str) -> bool:
    papers = _dev_get_papers(uid)
    _DEV_STORE[_dev_key(uid)] = [p for p in papers if p["paper_id"] != paper_id]
    return True

def _dev_update_notes(uid: str, paper_id: str, notes: str, tags: list[str] | None = None) -> bool:
    papers = _dev_get_papers(uid)
    for p in papers:
        if p["paper_id"] == paper_id:
            p["notes"] = notes
            if tags is not None:
                p["tags"] = tags
            return True
    return False
