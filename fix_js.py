import re

files = ['templates/index.html', 'templates/online.html', 'templates/playground.html']

for file in files:
    with open(file, 'r', encoding='utf-8') as f:
        content = f.read()

    # Change drawField() gradient colors
    content = content.replace("grad.addColorStop(0,   '#27a85f');", "grad.addColorStop(0,   '#0f172a');")
    content = content.replace("grad.addColorStop(0.5, '#2d8a50');", "grad.addColorStop(0.5, '#1e293b');")
    content = content.replace("grad.addColorStop(1,   '#27a85f');", "grad.addColorStop(1,   '#0f172a');")
    
    # Alternatively, if they have different spacing:
    content = re.sub(r"grad\.addColorStop\(0,\s*['\"]#27a85f['\"]\);", "grad.addColorStop(0, '#0f172a');", content)
    content = re.sub(r"grad\.addColorStop\(0\.5,\s*['\"]#2d8a50['\"]\);", "grad.addColorStop(0.5, '#1e293b');", content)
    content = re.sub(r"grad\.addColorStop\(1,\s*['\"]#27a85f['\"]\);", "grad.addColorStop(1, '#0f172a');", content)

    # Change field line colors to match dark mode better
    content = re.sub(r"ctx\.strokeStyle\s*=\s*['\"]rgba\(255,255,255,\.65\)['\"];", "ctx.strokeStyle = 'rgba(255,255,255,.2)';", content)
    
    # Change background of canvas wrap
    content = content.replace('.canvas-wrap{\n  border-radius:16px;overflow:hidden;\n  box-shadow:0 8px 32px rgba(80,120,200,.15);\n  position:relative;cursor:default;\n}', 
                              '.canvas-wrap{\n  border-radius:20px;overflow:hidden;\n  box-shadow:0 8px 32px rgba(0,0,0,.4);\n  position:relative;cursor:default;\n  border:1px solid rgba(255,255,255,.1);\n}')

    with open(file, 'w', encoding='utf-8') as f:
        f.write(content)

# Fix style.css again to ensure .glass, .glass-card is correct
with open('static/style.css', 'r', encoding='utf-8') as f:
    style_content = f.read()

# Make sure .glass overrides don't exist
style_content = re.sub(r'\.glass\s*\{[^}]*\}', '', style_content, flags=re.DOTALL)
style_content = re.sub(r'\.glass-card\s*\{[^}]*\}', '', style_content, flags=re.DOTALL)

glass_cls = '''
/* Glass Component */
.glass, .glass-card {
  background: var(--glass-bg) !important;
  border: 1px solid var(--glass-border) !important;
  box-shadow: var(--glass-shadow) !important;
  backdrop-filter: blur(var(--blur)) !important;
  -webkit-backdrop-filter: blur(var(--blur)) !important;
  border-radius: 20px !important;
  transition: border-color 0.3s ease, background 0.3s ease;
}

.glass:hover, .glass-card:hover {
  border-color: var(--glass-border-light) !important;
  background: var(--glass-bg-hover) !important;
}
'''

style_content = style_content.replace('/* Typography */', glass_cls + '\n/* Typography */')

with open('static/style.css', 'w', encoding='utf-8') as f:
    f.write(style_content)
