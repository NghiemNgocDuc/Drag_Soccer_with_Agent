import glob

for f in glob.glob('templates/*.html'):
    with open(f, 'r', encoding='utf-8') as file:
        content = file.read()
    
    if '<a href="/tournaments"' not in content:
        content = content.replace('<a href="/leaderboard"', '<a href="/tournaments" class="nav-link">Tournaments</a>\n    <a href="/leaderboard"')
        with open(f, 'w', encoding='utf-8') as file:
            file.write(content)
