"""Teams catalog — 48 World Cup 2026 nations, pick a team instead of just colors, with named players.

2 players cannot choose the same team (enforced in app.py room choose).
Each team has 11 canonical names (GK first) so 1-11 player counts are covered.
Colors are primary/secondary hex for kit + crest.
Source: FIFA 2026 qualified list (Reuters/Sky 2026-04-01) — 48 teams.
"""
from __future__ import annotations

TEAMS: list[dict] = [
    {"id": "usa", "name": "USA", "crest": "", "primary": "#002868", "secondary": "#BF0A30", "accent": "#FFFFFF", "players": ["Turner", "Dest", "Robinson", "Adams", "McKennie", "Pulisic", "Weah", "Reyna", "Balogun", "Musah", "Pepi"]},
    {"id": "mexico", "name": "Mexico", "crest": "", "primary": "#006847", "secondary": "#CE1126", "accent": "#FFFFFF", "players": ["Ochoa", "Araujo", "Montes", "Edson", "Chávez", "Lozano", "Jiménez", "Vega", "Antuna", "Herrera", "Martín"]},
    {"id": "canada", "name": "Canada", "crest": "", "primary": "#FF0000", "secondary": "#FFFFFF", "accent": "#FF0000", "players": ["Borjan", "Johnston", "Miller", "Hutchinson", "Eustáquio", "Davies", "David", "Larin", "Buchanan", "Osorio", "Hoilett"]},
    {"id": "brazil", "name": "Brazil", "crest": "", "primary": "#009739", "secondary": "#002776", "accent": "#FEDF00", "players": ["Alisson", "Marquinhos", "Militão", "Casemiro", "Paquetá", "Vinícius Jr.", "Neymar", "Rodrygo", "Raphinha", "Guimarães", "Richarlison"]},
    {"id": "argentina", "name": "Argentina", "crest": "", "primary": "#75AADB", "secondary": "#FFFFFF", "accent": "#FCBF49", "players": ["Martínez", "Romero", "Otamendi", "De Paul", "Mac Allister", "Messi", "Álvarez", "Lautaro", "Di María", "Paredes", "Fernández"]},
    {"id": "france", "name": "France", "crest": "", "primary": "#002395", "secondary": "#ED2939", "accent": "#FFFFFF", "players": ["Lloris", "Varane", "Koundé", "Kanté", "Rabiot", "Griezmann", "Mbappé", "Giroud", "Dembélé", "Tchouaméni", "Hernández"]},
    {"id": "germany", "name": "Germany", "crest": "", "primary": "#000000", "secondary": "#DD0000", "accent": "#FFCE00", "players": ["Neuer", "Rüdiger", "Süle", "Kimmich", "Kroos", "Müller", "Havertz", "Sané", "Gnabry", "Gündogan", "Musiala"]},
    {"id": "spain", "name": "Spain", "crest": "", "primary": "#C60B1E", "secondary": "#FFC400", "accent": "#C60B1E", "players": ["Simón", "Ramos", "Piqué", "Busquets", "Pedri", "Morata", "Olmo", "Ferran", "Rodri", "Gavi", "Williams"]},
    {"id": "england", "name": "England", "crest": "󠁧󠁢󠁥󠁮󠁧󠁿", "primary": "#FFFFFF", "secondary": "#CE1126", "accent": "#00247D", "players": ["Pickford", "Stones", "Walker", "Bellingham", "Saka", "Kane", "Foden", "Rice", "Grealish", "Alexander-Arnold", "Rashford"]},
    {"id": "portugal", "name": "Portugal", "crest": "", "primary": "#006600", "secondary": "#FF0000", "accent": "#FFCC00", "players": ["Patrício", "Pepe", "Dias", "Bruno F.", "Bernardo", "Ronaldo", "Félix", "Leão", "Cancelo", "Palhinha", "Vitinha"]},
    {"id": "netherlands", "name": "Netherlands", "crest": "", "primary": "#FF7700", "secondary": "#FFFFFF", "accent": "#002395", "players": ["Bijlow", "Van Dijk", "De Ligt", "De Jong", "Depay", "Gakpo", "Bergwijn", "Wijnaldum", "De Roon", "Dumfries", "Blind"]},
    {"id": "japan", "name": "Japan", "crest": "", "primary": "#FFFFFF", "secondary": "#BC002D", "accent": "#00247D", "players": ["Gonda", "Tomiyasu", "Taniguchi", "Endo", "Kamada", "Mitoma", "Kubo", "Asano", "Minamino", "Morita", "Ito"]},
    {"id": "australia", "name": "Australia", "crest": "", "primary": "#00843D", "secondary": "#FFCD00", "accent": "#00843D", "players": ["Ryan", "Souttar", "Rowles", "Mooy", "Irvine", "Leckie", "Duke", "Goodwin", "Hrustic", "Behich", "Maclaren"]},
    {"id": "iran", "name": "Iran", "crest": "", "primary": "#239F40", "secondary": "#DA0000", "accent": "#FFFFFF", "players": ["Beiranvand", "Hosseini", "Mohammadi", "Ezztolahi", "Hajsafi", "Jahanbakhsh", "Azmoun", "Taremi", "Ghoddos", "Amiri", "Ansarifard"]},
    {"id": "korea_rep", "name": "Korea Republic", "crest": "", "primary": "#C60C30", "secondary": "#0047A0", "accent": "#FFFFFF", "players": ["Kim S.G.", "Kim M.J.", "Kim J.S.", "Hwang I.B.", "Lee K.I.", "Son H.M.", "Hwang H.C.", "Cho G.S.", "Lee J.S.", "Jung W.Y.", "Kwon C.H."]},
    {"id": "saudi_arabia", "name": "Saudi Arabia", "crest": "", "primary": "#006C35", "secondary": "#FFFFFF", "accent": "#006C35", "players": ["Al-Owais", "Al-Bulaihi", "Al-Ghannam", "Al-Dawsari", "Kanno", "Al-Faraj", "Al-Shehri", "Al-Buraikan", "Al-Malki", "Abdulhamid", "Al-Amri"]},
    {"id": "qatar", "name": "Qatar", "crest": "", "primary": "#8A1538", "secondary": "#FFFFFF", "accent": "#8A1538", "players": ["Al-Sheeb", "Ro-Ro", "Khader", "Hassan", "Al-Haydos", "Afif", "Ali", "Boudiaf", "Mendes", "Abdurisag", "Madibo"]},
    {"id": "uzbekistan", "name": "Uzbekistan", "crest": "", "primary": "#1EB53A", "secondary": "#0099B5", "accent": "#CE1126", "players": ["Yusupov", "Ashurmatov", "Eshmurodov", "Shukurov", "Hamrobekov", "Masharipov", "Shomurodov", "Urunov", "Fayzullaev", "Alikulov", "Erkinov"]},
    {"id": "jordan", "name": "Jordan", "crest": "", "primary": "#000000", "secondary": "#CE1126", "accent": "#007A3D", "players": ["Abu Laila", "Al-Arab", "Nasib", "Al-Rashdan", "Al-Rawabdeh", "Al-Taamari", "Olwan", "Al-Naimat", "Sadeh", "Hadad", "Abu Hashish"]},
    {"id": "iraq", "name": "Iraq", "crest": "", "primary": "#007A3D", "secondary": "#FFFFFF", "accent": "#CE1126", "players": ["Hassan", "Sulaka", "Nadhim", "Amir", "Bayesh", "Ali J.", "Hussein", "Mohanad", "Al-Hamadi", "Attwan", "Putros"]},
    {"id": "uruguay", "name": "Uruguay", "crest": "", "primary": "#75AADB", "secondary": "#FFFFFF", "accent": "#FCD116", "players": ["Rochet", "Giménez", "Araújo", "Valverde", "Bentancur", "Núñez", "Suárez", "Cavani", "De Arrascaeta", "Ugarte", "Viña"]},
    {"id": "ecuador", "name": "Ecuador", "crest": "", "primary": "#FFDD00", "secondary": "#034EA2", "accent": "#ED1C24", "players": ["Galíndez", "Torres", "Hincapié", "Caicedo", "Gruezo", "Valencia", "Estrada", "Mena", "Plata", "Preciado", "Estupiñán"]},
    {"id": "colombia", "name": "Colombia", "crest": "", "primary": "#FFCD00", "secondary": "#003087", "accent": "#C8102E", "players": ["Ospina", "Mina", "Sánchez", "Uribe", "Lerma", "James", "Díaz", "Zapata", "Cuadrado", "Muñoz", "Arias"]},
    {"id": "paraguay", "name": "Paraguay", "crest": "", "primary": "#D52B1E", "secondary": "#0038A8", "accent": "#FFFFFF", "players": ["Silva", "Gómez", "Alderete", "Cubas", "Villalba", "Almirón", "Ávalos", "Enciso", "Sosa", "Giménez", "Arzamendia"]},
    {"id": "morocco", "name": "Morocco", "crest": "", "primary": "#C1272D", "secondary": "#006233", "accent": "#C1272D", "players": ["Bounou", "Hakimi", "Saïss", "Aguerd", "Amrabat", "Ziyech", "Hakimi", "En-Nesyri", "Boufal", "Mazraoui", "Ounahi"]},
    {"id": "tunisia", "name": "Tunisia", "crest": "", "primary": "#E70013", "secondary": "#FFFFFF", "accent": "#E70013", "players": ["Dahmen", "Talbi", "Meriah", "Skhiri", "Laïdouni", "Msakni", "Jebali", "Ben Slimane", "Abdi", "Valery", "Kechrida"]},
    {"id": "egypt", "name": "Egypt", "crest": "", "primary": "#CE1126", "secondary": "#FFFFFF", "accent": "#000000", "players": ["El Shenawy", "Hegazi", "Gaber", "Elneny", "Fathi", "Salah", "Trézéguet", "Marmoush", "Hamdy", "Ashour", "Hamed"]},
    {"id": "algeria", "name": "Algeria", "crest": "", "primary": "#006233", "secondary": "#FFFFFF", "accent": "#D21034", "players": ["M'Bolhi", "Mandi", "Bensebaini", "Bennacer", "Mahrez", "Slimani", "Feghouli", "Belaïli", "Bounedjah", "Bentaleb", "Atal"]},
    {"id": "ghana", "name": "Ghana", "crest": "", "primary": "#CE1126", "secondary": "#FCD116", "accent": "#006B3F", "players": ["Ati-Zigi", "Amartey", "Djiku", "Partey", "Kudus", "Ayew", "Williams", "Semenyo", "Jordan", "Salisu", "Mensah"]},
    {"id": "cape_verde", "name": "Cape Verde", "crest": "", "primary": "#003893", "secondary": "#CF2027", "accent": "#FCD116", "players": ["Vozinha", "Lopes", "Costa", "Bebe", "Mendes", "Monteiro", "Garry", "Tavares", "Semedo", "Fortes", "Andrade"]},
    {"id": "ivory_coast", "name": "Ivory Coast", "crest": "", "primary": "#FF8200", "secondary": "#FFFFFF", "accent": "#009A44", "players": ["Fofana", "Bailly", "Kessié", "Sangaré", "Pepe", "Haller", "Zaha", "Gradel", "Kossounou", "Singo", "Diomandé"]},
    {"id": "senegal", "name": "Senegal", "crest": "", "primary": "#00853F", "secondary": "#FDEF42", "accent": "#E31E24", "players": ["Mendy", "Koulibaly", "Diallo", "Gueye", "Mané", "Sarr", "Jackson", "Diatta", "Jakobs", "Ciss", "P. Gueye"]},
    {"id": "south_africa", "name": "South Africa", "crest": "", "primary": "#007A4D", "secondary": "#FFFFFF", "accent": "#FFB612", "players": ["Williams", "Mvala", "Xulu", "Mokoena", "Morena", "Tau", "Mayambela", "Maseko", "Mudau", "Aubaas", "Modiba"]},
    {"id": "tunisia", "name": "Tunisia", "crest": "", "primary": "#E70013", "secondary": "#FFFFFF", "accent": "#E70013", "players": ["Dahmen", "Talbi", "Meriah", "Skhiri", "Laïdouni", "Msakni", "Jebali", "Ben Slimane", "Abdi", "Valery", "Kechrida"]},
    {"id": "senegal", "name": "Senegal", "crest": "", "primary": "#00853F", "secondary": "#FDEF42", "accent": "#E31E24", "players": ["Mendy", "Koulibaly", "Diallo", "Gueye", "Mané", "Sarr", "Jackson", "Diatta", "Jakobs", "Ciss", "P. Gueye"]},
    {"id": "austria", "name": "Austria", "crest": "", "primary": "#ED2939", "secondary": "#FFFFFF", "accent": "#ED2939", "players": ["Bachmann", "Alaba", "Posch", "Sabitzer", "Laimer", "Arnautović", "Baumgartner", "Gregoritsch", "Seiwald", "Wimmer", "Danso"]},
    {"id": "belgium", "name": "Belgium", "crest": "", "primary": "#ED2939", "secondary": "#FAE042", "accent": "#000000", "players": ["Courtois", "Vertonghen", "Alderweireld", "De Bruyne", "Hazard", "Lukaku", "Doku", "Trossard", "Tielemans", "Castagne", "Witsel"]},
    {"id": "croatia", "name": "Croatia", "crest": "", "primary": "#FF0000", "secondary": "#FFFFFF", "accent": "#171796", "players": ["Livaković", "Vida", "Gvardiol", "Modrić", "Kovačić", "Perišić", "Kramarić", "Livaja", "Brozović", "Juranović", "Sosa"]},
    {"id": "switzerland", "name": "Switzerland", "crest": "", "primary": "#DA020E", "secondary": "#FFFFFF", "accent": "#DA020E", "players": ["Sommer", "Akanji", "Elvedi", "Xhaka", "Freuler", "Shaqiri", "Embolo", "Seferović", "Zakaria", "Widmer", "Rodríguez"]},
    {"id": "scotland", "name": "Scotland", "crest": "󠁧󠁢󠁳󠁣󠁴󠁿", "primary": "#005EB8", "secondary": "#FFFFFF", "accent": "#005EB8", "players": ["Gunn", "Tierney", "Robertson", "McGregor", "McGinn", "McTominay", "Adams", "Christie", "Armstrong", "Hendry", "Dykes"]},
    {"id": "czechia", "name": "Czechia", "crest": "", "primary": "#11457E", "secondary": "#FFFFFF", "accent": "#D7141A", "players": ["Vaclík", "Coufal", "Kalas", "Souček", "Darida", "Schick", "Hložek", "Kuchta", "Barák", "Holeš", "Bořil"]},
    {"id": "bosnia", "name": "Bosnia and Herzegovina", "crest": "", "primary": "#002395", "secondary": "#FECB00", "accent": "#002395", "players": ["Šehić", "Kolašinac", "Hadžikadunić", "Pjanić", "Krunić", "Džeko", "Demić", "Prevljak", "Stevanović", "Civic", "Hadziahmetović"]},
    {"id": "sweden", "name": "Sweden", "crest": "", "primary": "#006AA7", "secondary": "#FECC02", "accent": "#006AA7", "players": ["Olsen", "Lindelöf", "Augustinsson", "Ekdal", "Forsberg", "Isak", "Gyökeres", "Kulusevski", "Olsson", "Claesson", "Elanga"]},
    {"id": "turkiye", "name": "Türkiye", "crest": "", "primary": "#E30A17", "secondary": "#FFFFFF", "accent": "#E30A17", "players": ["Çakır", "Söyüncü", "Demiral", "Çalhanoğlu", "Yazıcı", "Yılmaz", "Ünal", "Aktürkoğlu", "Kökçü", "Ayhan", "Yıldız"]},
    {"id": "norway", "name": "Norway", "crest": "", "primary": "#EF2B2D", "secondary": "#002868", "accent": "#FFFFFF", "players": ["Nyland", "Ajer", "Østigård", "Ødegaard", "Berge", "Haaland", "Sørloth", "Elyounoussi", "Thorsby", "Ryerson", "Strandberg"]},
    {"id": "panama", "name": "Panama", "crest": "", "primary": "#DA121A", "secondary": "#072357", "accent": "#FFFFFF", "players": ["Mosquera", "Davis", "Escobar", "Bárcenas", "Godoy", "Carrasquilla", "Fajardo", "Díaz", "Waterman", "Blackman", "Murillo"]},
    {"id": "curacao", "name": "Curaçao", "crest": "", "primary": "#002B7F", "secondary": "#F9E814", "accent": "#002B7F", "players": ["Room", "Gaari", "Martina", "Bacuna", "Kuwas", "Janga", "Antonisse", "Gorré", "Felida", "Van den Hurk", "Anita"]},
    {"id": "haiti", "name": "Haiti", "crest": "", "primary": "#00209F", "secondary": "#D21034", "accent": "#FFFFFF", "players": ["Placide", "Adé", "Christian", "Saba", "Nazon", "Pierrot", "Etienne", "Antoine", "Jacques", "Alceus", "Arcus"]},
    {"id": "new_zealand", "name": "New Zealand", "crest": "", "primary": "#000000", "secondary": "#FFFFFF", "accent": "#00247D", "players": ["Marinovic", "Boxall", "Smith", "Wood", "Barbarouses", "McCowatt", "Stamenic", "Bell", "Garrick", "Pijnaker", "Reid"]},
    {"id": "qatar", "name": "Qatar", "crest": "", "primary": "#8A1538", "secondary": "#FFFFFF", "accent": "#8A1538", "players": ["Al-Sheeb", "Ro-Ro", "Khader", "Hassan", "Al-Haydos", "Afif", "Ali", "Boudiaf", "Mendes", "Abdurisag", "Madibo"]},    {"id": "dr_congo", "name": "DR Congo", "crest": "", "primary": "#007FFF", "secondary": "#CE1026", "accent": "#F7D618", "players": ["Mpasi", "Mbemba", "Masuaku", "Kakuta", "Bakambu", "Wissa", "Bongonda", "Moutoussamy", "Kayembe", "Banza", "Elia"]},
    {"id": "cape_verde", "name": "Cape Verde", "crest": "", "primary": "#003893", "secondary": "#CF2027", "accent": "#FCD116", "players": ["Vozinha", "Lopes", "Costa", "Bebe", "Mendes", "Monteiro", "Garry", "Tavares", "Semedo", "Fortes", "Andrade"]},
    {"id": "scotland", "name": "Scotland", "crest": "󠁧󠁢󠁳󠁣󠁴󠁿", "primary": "#005EB8", "secondary": "#FFFFFF", "accent": "#005EB8", "players": ["Gunn", "Tierney", "Robertson", "McGregor", "McGinn", "McTominay", "Adams", "Christie", "Armstrong", "Hendry", "Dykes"]},
]

# Deduplicate by id (keep last)
_seen = {}
for tm in TEAMS:
    _seen[tm["id"]] = tm
TEAMS = list(_seen.values())
TEAMS_BY_ID = {t["id"]: t for t in TEAMS}

def get_team(team_id: str) -> dict | None:
    return TEAMS_BY_ID.get(team_id)

def list_teams() -> list[dict]:
    return [dict(t) for t in TEAMS]

def team_for_players(team_id: str, count: int) -> list[str]:
    """First `count` names for that team, padded with 'Player N' if needed."""
    t = get_team(team_id)
    if not t:
        return [f"Player {i+1}" for i in range(count)]
    names = t["players"]
    out = []
    for i in range(count):
        out.append(names[i % len(names)])
    return out
