"""Managers tied to formations — each team has a manager with a preferred 7-player formation.

Formation string for 7 players: GK + 6 outfield as DEF-MID-FWD, e.g. "3-2-1" = 3 DEF, 2 MID, 1 FWD.
User can change formation before game starts; manager's default is just the initial pick.
Referee names are also here — a random referee is chosen per match and shown.
"""
from __future__ import annotations
import random

# 7-player formations (GK + 6 outfield). Most popular 7v7 per web (2-3-1 #1, 3-2-1 #2, diamond etc.)
# 10 most popular: 2-3-1, 3-2-1, 2-1-2-1, 3-1-2, 2-2-2, 1-3-2, 3-3-0, 1-4-1, 2-1-3, 1-2-3
FORMATIONS_7: dict[str, tuple] = {
    "2-3-1": (2,3,1),      # standard — most popular
    "3-2-1": (3,2,1),      # defensive
    "2-1-2-1": (2,1,2,1),  # diamond
    "3-1-2": (3,1,2),
    "2-2-2": (2,2,2),      # balanced
    "1-3-2": (1,3,2),
    "3-3-0": (3,3,0),
    "1-4-1": (1,4,1),      # ultra attacking diamond
    "2-1-3": (2,1,3),
    "1-2-3": (1,2,3),
}
DEFAULT_FORMATION_7 = "2-3-1"

# Real managers (WC2026 era) mapped to preferred formation
MANAGERS: dict[str, dict] = {
    "brazil": {"name": "Dorival Júnior", "formation": "2-3-1", "style": "Samba Possession"},
    "argentina": {"name": "Lionel Scaloni", "formation": "3-2-1", "style": "Compact Counter"},
    "france": {"name": "Didier Deschamps", "formation": "3-3-0", "style": "Balanced"},
    "germany": {"name": "Julian Nagelsmann", "formation": "3-2-1", "style": "High Press"},
    "spain": {"name": "Luis de la Fuente", "formation": "3-3-0", "style": "Tiki-Taka"},
    "england": {"name": "Gareth Southgate", "formation": "3-2-1", "style": "Direct"},
    "portugal": {"name": "Roberto Martínez", "formation": "2-3-1", "style": "Possession"},
    "netherlands": {"name": "Ronald Koeman", "formation": "3-2-1", "style": "Total Football"},
    "usa": {"name": "Gregg Berhalter", "formation": "2-2-2", "style": "Pressing"},
    "mexico": {"name": "Jaime Lozano", "formation": "3-2-1", "style": "Counter"},
    "canada": {"name": "Jesse Marsch", "formation": "2-3-1", "style": "High Tempo"},
    "japan": {"name": "Hajime Moriyasu", "formation": "3-2-1", "style": "Quick Transition"},
    "australia": {"name": "Graham Arnold", "formation": "3-2-1", "style": "Physical"},
    "iran": {"name": "Amir Ghalenoei", "formation": "3-2-1", "style": "Compact"},
    "korea_rep": {"name": "Jürgen Klinsmann", "formation": "2-3-1", "style": "Counter"},
    "saudi_arabia": {"name": "Roberto Mancini", "formation": "3-2-1", "style": "Possession"},
    "qatar": {"name": "Tintín Márquez", "formation": "3-2-1", "style": "Possession"},
    "uzbekistan": {"name": "Srečko Katanec", "formation": "2-2-2", "style": "Balanced"},
    "jordan": {"name": "Hussein Amouta", "formation": "3-2-1", "style": "Defensive"},
    "iraq": {"name": "Jesús García", "formation": "3-2-1", "style": "Counter"},
    "uruguay": {"name": "Marcelo Bielsa", "formation": "3-3-0", "style": "Bielsa Press"},
    "ecuador": {"name": "Félix Sánchez", "formation": "2-3-1", "style": "Possession"},
    "colombia": {"name": "Néstor Lorenzo", "formation": "2-2-2", "style": "Balanced"},
    "paraguay": {"name": "Daniel Garnero", "formation": "3-2-1", "style": "Compact"},
    "morocco": {"name": "Walid Regragui", "formation": "3-2-1", "style": "Compact Block"},
    "tunisia": {"name": "Jalel Kadri", "formation": "3-2-1", "style": "Defensive"},
    "egypt": {"name": "Rui Vitória", "formation": "2-3-1", "style": "Counter"},
    "algeria": {"name": "Djamel Belmadi", "formation": "3-2-1", "style": "Possession"},
    "ghana": {"name": "Otto Addo", "formation": "2-3-1", "style": "Direct"},
    "cape_verde": {"name": "Bubista", "formation": "3-2-1", "style": "Counter"},
    "ivory_coast": {"name": "Emerse Faé", "formation": "2-3-1", "style": "Physical"},
    "senegal": {"name": "Aliou Cissé", "formation": "3-2-1", "style": "Physical"},
    "south_africa": {"name": "Hugo Broos", "formation": "3-2-1", "style": "Balanced"},
    "austria": {"name": "Ralf Rangnick", "formation": "2-2-2", "style": "Gegenpress"},
    "belgium": {"name": "Domenico Tedesco", "formation": "3-2-1", "style": "Possession"},
    "croatia": {"name": "Zlatko Dalić", "formation": "2-3-1", "style": "Midfield Control"},
    "switzerland": {"name": "Murat Yakin", "formation": "3-2-1", "style": "Balanced"},
    "scotland": {"name": "Steve Clarke", "formation": "3-2-1", "style": "Direct"},
    "czechia": {"name": "Ivan Hašek", "formation": "3-2-1", "style": "Counter"},
    "bosnia": {"name": "Savo Milošević", "formation": "2-2-2", "style": "Balanced"},
    "sweden": {"name": "Jon Dahl Tomasson", "formation": "2-3-1", "style": "Direct"},
    "turkiye": {"name": "Vincenzo Montella", "formation": "2-3-1", "style": "Possession"},
    "norway": {"name": "Ståle Solbakken", "formation": "3-2-1", "style": "Direct"},
    "panama": {"name": "Thomas Christiansen", "formation": "3-2-1", "style": "Counter"},
    "curacao": {"name": "Dick Advocaat", "formation": "3-2-1", "style": "Possession"},
    "haiti": {"name": "Gabriel Calderón", "formation": "2-2-2", "style": "Counter"},
    "new_zealand": {"name": "Darren Bazeley", "formation": "3-2-1", "style": "Physical"},
    "dr_congo": {"name": "Sébastien Desabre", "formation": "3-2-1", "style": "Counter"},
}

def get_manager(team_id: str) -> dict:
    return MANAGERS.get(team_id, {"name": "Manager", "formation": DEFAULT_FORMATION_7, "style": "Balanced"})

def list_formations() -> list[str]:
    return list(FORMATIONS_7.keys())

def formation_for_count(team_id: str, count: int) -> str:
    """For 7 players return manager's preferred; for other counts fallback to generic."""
    if count == 7:
        return get_manager(team_id)["formation"]
    # fallback to generic _home_positions logic for other counts
    return DEFAULT_FORMATION_7

# Referees
REFEREES: list[dict] = [
    {"name": "Pierluigi Collina", "country": "ITA"},
    {"name": "Björn Kuipers", "country": "NED"},
    {"name": "Szymon Marciniak", "country": "POL"},
    {"name": "Howard Webb", "country": "ENG"},
    {"name": "Néstor Pitana", "country": "ARG"},
    {"name": "Wilton Sampaio", "country": "BRA"},
    {"name": "Daniele Orsato", "country": "ITA"},
    {"name": "Cüneyt Çakır", "country": "TUR"},
    {"name": "Alireza Faghani", "country": "IRN"},
    {"name": "Bakary Gassama", "country": "GAM"},
    {"name": "Stéphanie Frappart", "country": "FRA"},
    {"name": "Anthony Taylor", "country": "ENG"},
]

def pick_referee(seed: str | None = None) -> dict:
    if seed:
        import hashlib
        h = int(hashlib.md5(seed.encode()).hexdigest()[:8], 16)
        return REFEREES[h % len(REFEREES)]
    return random.choice(REFEREES)
