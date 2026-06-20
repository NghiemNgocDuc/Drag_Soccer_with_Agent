import glob
import re

for filepath in glob.glob('templates/*.html'):
    if filepath.endswith('tournaments.html') or filepath.endswith('tournament_view.html') or filepath.endswith('replay.html'):
        continue

    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Rename
    content = content.replace('Ô Ăn Quan', 'Agent Soccer')

    # Add nav-link class to naked a tags inside nav-links
    # e.g. <a href="/">Game</a> -> <a href="/" class="nav-link">Game</a>
    content = re.sub(r'<a href="([^"]+)">([^<]+)</a>', r'<a href="\1" class="nav-link">\2</a>', content)
    
    # Fix active class (which becomes class="active" -> class="nav-link active")
    content = re.sub(r'<a href="([^"]+)" class="active">([^<]+)</a>', r'<a href="\1" class="nav-link active">\2</a>', content)

    # Replace the ugly logout form
    form_str = """<form method="POST" action="/auth/logout" style="display:inline">
      <button type="submit" class="btn-logout">Logout</button>
    </form>"""
    logout_link = '<a href="/auth/logout" class="nav-link" style="color:var(--accent-b);margin-left:8px">Logout</a>'
    content = content.replace(form_str, logout_link)
    
    # Replace the old ugly form variant from my_models
    form_str2 = """    <form method="POST" action="/auth/logout" style="display:inline">
      <button type="submit" class="btn-logout">Logout</button>
    </form>"""
    content = content.replace(form_str2, '    ' + logout_link)

    # Add Tournaments link if not present
    if 'href="/tournaments"' not in content:
        # insert before leaderboard
        content = content.replace('<a href="/leaderboard"', '<a href="/tournaments" class="nav-link">Tournaments</a>\n    <a href="/leaderboard"')

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

print("Fixed templates.")
