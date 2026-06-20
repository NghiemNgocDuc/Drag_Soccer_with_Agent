import re

# 1. Update style.css
with open('static/style.css', 'r', encoding='utf-8') as f:
    css = f.read()

# Font
css = re.sub(r"https://fonts.googleapis.com/css2\?family=Outfit[^']+", "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap", css)
css = css.replace("'Outfit'", "'Inter'")

# CSS Variables
css = re.sub(r'--text-main:\s*[^;]+;', '--text-main: #0f172a;', css)
css = re.sub(r'--text-muted:\s*[^;]+;', '--text-muted: #475569;', css)
css = re.sub(r'--text-dim:\s*[^;]+;', '--text-dim: #94a3b8;', css)

css = re.sub(r'--glass-bg:\s*[^!]+!important;', '--glass-bg: rgba(255, 255, 255, 0.75) !important;', css)
css = re.sub(r'--glass-bg:\s*[^;]+;', '--glass-bg: rgba(255, 255, 255, 0.75);', css)
css = re.sub(r'--glass-bg-hover:\s*[^;]+;', '--glass-bg-hover: rgba(255, 255, 255, 0.9);', css)
css = re.sub(r'--glass-border:\s*[^!]+!important;', '--glass-border: rgba(255, 255, 255, 0.5) !important;', css)
css = re.sub(r'--glass-border:\s*[^;]+;', '--glass-border: rgba(255, 255, 255, 0.5);', css)
css = re.sub(r'--glass-border-light:\s*[^;]+;', '--glass-border-light: rgba(255, 255, 255, 0.8);', css)
css = re.sub(r'--glass-shadow:\s*[^!]+!important;', '--glass-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.07) !important;', css)
css = re.sub(r'--glass-shadow:\s*[^;]+;', '--glass-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.07);', css)

# Body background
css = re.sub(r'background-color:\s*#020617;', 'background-color: #f8fafc;', css)
css = css.replace('rgba(2,6,23,0)', 'rgba(248,250,252,0)')

# Nav and inputs
css = re.sub(r'rgba\(15,\s*23,\s*42,\s*0\.65\)', 'rgba(255, 255, 255, 0.65)', css) # nav
css = re.sub(r'rgba\(255,\s*255,\s*255,\s*0\.05\)', 'rgba(59, 130, 246, 0.1)', css) # nav-link hover
css = re.sub(r'rgba\(255,\s*255,\s*255,\s*0\.03\)', 'rgba(255, 255, 255, 0.5)', css) # btn-ghost
css = re.sub(r'rgba\(15,\s*23,\s*42,\s*0\.5\)', 'rgba(255, 255, 255, 0.6)', css) # inputs
css = re.sub(r'rgba\(15,\s*23,\s*42,\s*0\.7\)', 'rgba(255, 255, 255, 0.9)', css) # inputs focus
css = re.sub(r'rgba\(255,\s*255,\s*255,\s*0\.04\)', 'rgba(59, 130, 246, 0.05)', css) # table hover

# Title gradient
css = css.replace('linear-gradient(135deg, #f8fafc 0%, #94a3b8 100%)', 'linear-gradient(135deg, #1e2d4f 0%, #3b82f6 100%)')

with open('static/style.css', 'w', encoding='utf-8') as f:
    f.write(css)

# 2. Update JS in templates
files = ['templates/index.html', 'templates/online.html', 'templates/playground.html']

for file in files:
    with open(file, 'r', encoding='utf-8') as f:
        content = f.read()

    # Revert to green canvas
    content = content.replace("grad.addColorStop(0, '#0f172a');", "grad.addColorStop(0, '#27a85f');")
    content = content.replace("grad.addColorStop(0.5, '#1e293b');", "grad.addColorStop(0.5, '#2d8a50');")
    content = content.replace("grad.addColorStop(1, '#0f172a');", "grad.addColorStop(1, '#27a85f');")
    
    # White lines on field
    content = content.replace("ctx.strokeStyle = 'rgba(255,255,255,.2)';", "ctx.strokeStyle = 'rgba(255,255,255,.65)';")
    
    # Drag event fixes
    content = content.replace("canvas.addEventListener('mousemove'", "window.addEventListener('mousemove'")
    content = content.replace("canvas.addEventListener('mouseup'", "window.addEventListener('mouseup'")
    content = content.replace("canvas.addEventListener('touchmove'", "window.addEventListener('touchmove'")
    content = content.replace("canvas.addEventListener('touchend'", "window.addEventListener('touchend'")
    
    # Remove mouseleave entirely
    content = re.sub(r"canvas\.addEventListener\('mouseleave',\s*\(\)\s*=>\s*\{[^}]*\}\);", "", content, flags=re.DOTALL)

    with open(file, 'w', encoding='utf-8') as f:
        f.write(content)
