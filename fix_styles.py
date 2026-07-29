import os
import re

templates_dir = "templates"

link_tag = '<link rel="stylesheet" href="/static/style.css">\n'

for filename in os.listdir(templates_dir):
    if not filename.endswith(".html"):
        continue
        
    filepath = os.path.join(templates_dir, filename)
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # If already has link tag, skip
    if "style.css" in content:
        print(f"Skipping {filename} (already linked)")
        continue

    # Remove the massive <style>...</style> block
    # We'll use a regex to match <style> to </style> 
    # but we only want to remove it if it's the massive one with root variables, etc.
    # To be safe, we'll just replace the first <style>...</style> that contains "--glass-bg"
    # Actually, some templates might have other styles, let's just replace any <style> block that contains ':root' or '--glass-bg'
    
    pattern = re.compile(r'<style>.*?</style>', re.DOTALL)
    
    def replacer(match):
        text = match.group(0)
        if "--glass-bg" in text or ":root" in text:
            return ""
        return text
    
    new_content = pattern.sub(replacer, content)
    
    # Add link tag inside <head> if it's not there
    if "<head>" in new_content:
        new_content = new_content.replace("<head>", f"<head>\n{link_tag}")
    else:
        # Just prepend it if no head
        new_content = link_tag + new_content

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(new_content)
        
    print(f"Updated {filename}")
