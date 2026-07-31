import os
import re

templates_dir = "templates"
link_tag = '<link rel="stylesheet" href="/static/style.css">\n'

def clean_css(content):
    # Regexes to remove known generic blocks
    patterns = [
        r'\*,\*::before,\*::after\{[^}]*\}',
        r'\*\{box-sizing:border-box;margin:0;padding:0\}',
        r':root\{[^}]*\}',
        r'body\{[^}]*\}',
        r'@keyframes bg-flow\{[^}]*\}',
        r'\.nebula\{[^}]*\}',
        r'\.nebula::before\{[^}]*\}',
        r'\.nebula::after\{[^}]*\}',
        r'@keyframes drift\s*\{[^}]*\}',
        r'@keyframes drift2\s*\{[^}]*\}',
        r'\.glass\{[^}]*\}',
        r'nav\{[^}]*\}',
        r'\.nav-brand\{[^}]*\}',
        r'\.nav-links\{[^}]*\}',
        r'\.nav-links a\{[^}]*\}',
        r'\.nav-links a:hover,\.nav-links a\.active\{[^}]*\}',
        r'\.nav-link\{[^}]*\}',
        r'\.nav-link:hover,\.nav-link\.active\{[^}]*\}',
        r'\.nav-user\{[^}]*\}',
        r'\.btn-nav-logout\{[^}]*\}',
        r'\.btn-logout\{[^}]*\}',
        r'\.btn-primary\{[^}]*\}',
        r'\.btn-primary:hover\{[^}]*\}',
        r'\.btn-danger\{[^}]*\}',
        r'\.btn-danger:hover\{[^}]*\}',
        r'\.btn-ghost\{[^}]*\}',
        r'\.btn-ghost:hover\{[^}]*\}',
        r'\.btn-ghost::after\{[^}]*\}',
        r'\.btn-success\{[^}]*\}',
        r'\.btn-success:hover\{[^}]*\}',
        r'\.btn\{[^}]*\}',
        r'\.btn:active\{[^}]*\}',
        r'\.btn::after\{[^}]*\}',
        r'\.btn:hover::after\{[^}]*\}',
        r'input\[type=text\],input\[type=search\],input\[type=password\],input\[type=email\]\{[^}]*\}',
        r'input\[type=text\]:focus,input\[type=search\]:focus,input\[type=password\]:focus,input\[type=email\]:focus\{[^}]*\}',
        r'input\[type=text\],input\[type=search\]\{[^}]*\}',
        r'input\[type=text\]:focus,input\[type=search\]:focus\{[^}]*\}',
        r'\.flash-msg\{[^}]*\}',
        r'\.flash-msg\.show\{[^}]*\}',
        r'\.flash-msg\.success\{[^}]*\}',
        r'\.flash-msg\.error\{[^}]*\}',
    ]
    
    # We only apply these to the content inside <style> tags
    def replace_style(match):
        style_content = match.group(0)
        for p in patterns:
            style_content = re.sub(p, '', style_content, flags=re.DOTALL)
        
        # also remove empty lines
        style_content = re.sub(r'\n\s*\n', '\n', style_content)
        
        # If style is empty except for tags, remove the whole thing
        inner = re.sub(r'</?style>', '', style_content).strip()
        if not inner:
            return ""
            
        return style_content
        
    return re.sub(r'<style>.*?</style>', replace_style, content, flags=re.DOTALL)

for filename in os.listdir(templates_dir):
    if not filename.endswith(".html"):
        continue
        
    # skip deleted templates
    if filename in ["index.html", "online.html", "replay.html"]:
        continue
        
    filepath = os.path.join(templates_dir, filename)
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Add link tag inside <head> if it's not there
    if "style.css" not in content:
        if "<head>" in content:
            content = content.replace("<head>", f"<head>\n{link_tag}")
        else:
            content = link_tag + content

    # Clean CSS
    new_content = clean_css(content)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(new_content)
        
    print(f"Cleaned {filename}")
