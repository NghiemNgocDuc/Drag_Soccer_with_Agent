import re

with open('static/style.css', 'r', encoding='utf-8') as f:
    content = f.read()

# We need to remove <style> and </style>
content = content.replace('<style>', '')
content = content.replace('</style>', '')

# We need to remove the appended :root, body, nav, .nebula, *, main rules from the specific sections.
# Let's split by INDEX.HTML SPECIFIC to find the appended part
parts = content.split('/* ---- INDEX.HTML SPECIFIC ---- */')

if len(parts) > 1:
    base_css = parts[0]
    appended_css = '/* ---- INDEX.HTML SPECIFIC ---- */' + parts[1]
    
    # Remove block functions
    def remove_block(pattern, text):
        return re.sub(pattern, '', text, flags=re.DOTALL)
    
    appended_css = remove_block(r':root\s*\{[^}]*\}', appended_css)
    appended_css = remove_block(r'\*\s*,\s*\*\s*::before\s*,\s*\*\s*::after\s*\{[^}]*\}', appended_css)
    appended_css = remove_block(r'\*\s*\{[^}]*\}', appended_css)
    appended_css = remove_block(r'body\s*\{[^}]*\}', appended_css)
    appended_css = remove_block(r'body::before\s*\{[^}]*\}', appended_css)
    appended_css = remove_block(r'body::after\s*\{[^}]*\}', appended_css)
    appended_css = remove_block(r'\.nebula\s*\{[^}]*\}', appended_css)
    appended_css = remove_block(r'\.nebula::before\s*\{[^}]*\}', appended_css)
    appended_css = remove_block(r'\.nebula::after\s*\{[^}]*\}', appended_css)
    appended_css = remove_block(r'@keyframes drift\s*\{[^}]*\}', appended_css)
    appended_css = remove_block(r'@keyframes drift2\s*\{[^}]*\}', appended_css)
    appended_css = remove_block(r'nav\s*,\s*main\s*\{[^}]*\}', appended_css)
    appended_css = remove_block(r'nav\s*,\s*div\.page\s*\{[^}]*\}', appended_css)
    appended_css = remove_block(r'nav\s*\{[^}]*\}', appended_css)
    appended_css = remove_block(r'\.nav-brand\s*\{[^}]*\}', appended_css)
    appended_css = remove_block(r'\.nav-links\s*\{[^}]*\}', appended_css)
    appended_css = remove_block(r'\.nav-links\s*a\s*\{[^}]*\}', appended_css)
    appended_css = remove_block(r'\.nav-links\s*a:hover\s*,\s*\.nav-links\s*a\.active\s*\{[^}]*\}', appended_css)
    appended_css = remove_block(r'\.nav-user\s*\{[^}]*\}', appended_css)
    appended_css = remove_block(r'\.btn-nav-logout\s*\{[^}]*\}', appended_css)
    appended_css = remove_block(r'\.btn-nav-logout:hover\s*\{[^}]*\}', appended_css)
    appended_css = remove_block(r'\.btn-logout\s*\{[^}]*\}', appended_css)
    appended_css = remove_block(r'\.btn-logout:hover\s*\{[^}]*\}', appended_css)
    appended_css = remove_block(r'\.nav-link\s*\{[^}]*\}', appended_css)
    appended_css = remove_block(r'\.nav-link:hover\s*,\s*\.nav-link\.active\s*\{[^}]*\}', appended_css)
    appended_css = remove_block(r'main\s*\{[^}]*\}', appended_css)
    appended_css = remove_block(r'\.glass\s*\{[^}]*\}', appended_css)
    
    # Write back
    with open('static/style.css', 'w', encoding='utf-8') as f:
        f.write(base_css + appended_css)
