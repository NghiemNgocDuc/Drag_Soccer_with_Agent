import re

css_top = """@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

:root {
  /* Brand Colors */
  --accent-a: #3b82f6; /* Blue for Player A */
  --accent-b: #ef4444; /* Red for AI / Player B */
  --accent-success: #10b981;
  --accent-warning: #f59e0b;
  --accent-glow: rgba(59, 130, 246, 0.2);
  
  /* Text Colors */
  --text-main: #0f172a;
  --text-muted: #475569;
  --text-dim: #94a3b8;
  
  /* Glass Variables */
  --glass-bg: rgba(255, 255, 255, 0.75);
  --glass-bg-hover: rgba(255, 255, 255, 0.9);
  --glass-border: rgba(255, 255, 255, 0.5);
  --glass-border-light: rgba(255, 255, 255, 0.8);
  --glass-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.07);
  --blur: 24px;
}

* {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

body {
  font-family: 'Inter', system-ui, sans-serif;
  color: var(--text-main);
  background-color: #f8fafc;
  min-height: 100vh;
  overflow-x: hidden;
  position: relative;
}

/* Dynamic Animated Background */
body::before, body::after {
  content: '';
  position: fixed;
  border-radius: 50%;
  filter: blur(120px);
  z-index: -1;
  pointer-events: none;
  animation: float-blobs 25s ease-in-out infinite alternate;
}

body::before {
  top: -10%;
  left: -10%;
  width: 60vw;
  height: 60vh;
  background: radial-gradient(circle, rgba(59,130,246,0.08) 0%, rgba(248,250,252,0) 70%);
}

body::after {
  bottom: -10%;
  right: -10%;
  width: 50vw;
  height: 50vh;
  background: radial-gradient(circle, rgba(239,68,68,0.06) 0%, rgba(248,250,252,0) 70%);
  animation-delay: -12s;
  animation-duration: 30s;
}

@keyframes float-blobs {
  0% { transform: translate(0, 0) scale(1); }
  50% { transform: translate(5vw, 5vh) scale(1.1); }
  100% { transform: translate(-5vw, 10vh) scale(0.9); }
}

/* Layout */
main {
  max-width: 1000px;
  margin: 0 auto;
  padding: 32px 16px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* Glass Component */
.glass, .glass-card {
  background: var(--glass-bg);
  border: 1px solid var(--glass-border);
  box-shadow: var(--glass-shadow);
  backdrop-filter: blur(var(--blur));
  -webkit-backdrop-filter: blur(var(--blur));
  border-radius: 20px;
  transition: border-color 0.3s ease, background 0.3s ease;
}

.glass:hover, .glass-card:hover {
  border-color: var(--glass-border-light);
  background: var(--glass-bg-hover);
}

/* Typography */
h1, h2, h3 {
  font-weight: 700;
  letter-spacing: -0.02em;
}

.title-gradient {
  background: linear-gradient(135deg, #1e2d4f 0%, #3b82f6 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

/* Navbar */
nav {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 14px 28px;
  background: rgba(255, 255, 255, 0.65);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border-bottom: 1px solid var(--glass-border);
  position: sticky;
  top: 0;
  z-index: 100;
}

.nav-brand {
  font-size: 1.25rem;
  font-weight: 800;
  color: var(--text-main);
  text-decoration: none;
  letter-spacing: -0.02em;
  background: linear-gradient(135deg, #1e2d4f 0%, #3b82f6 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}

.nav-links {
  display: flex;
  gap: 6px;
  margin-left: auto;
  align-items: center;
}

.nav-link {
  padding: 8px 16px;
  border-radius: 12px;
  font-size: 0.9rem;
  font-weight: 500;
  color: var(--text-muted);
  text-decoration: none;
  transition: all 0.2s ease;
}

.nav-link:hover, .nav-link.active {
  background: rgba(59, 130, 246, 0.1);
  color: var(--text-main);
}

.nav-user {
  font-size: 0.85rem;
  color: var(--text-dim);
  padding: 6px 0 6px 16px;
  border-left: 1px solid var(--glass-border);
}

/* Buttons */
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 10px 20px;
  border-radius: 12px;
  border: 1px solid transparent;
  cursor: pointer;
  font-size: 0.9rem;
  font-weight: 600;
  font-family: inherit;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  outline: none;
  text-decoration: none;
}

.btn:active {
  transform: scale(0.97);
}

.btn-primary {
  background: linear-gradient(135deg, #2563eb, #3b82f6);
  color: #fff;
  border: 1px solid rgba(255, 255, 255, 0.5);
  box-shadow: 0 4px 15px rgba(59, 130, 246, 0.2);
}

.btn-primary:hover {
  background: linear-gradient(135deg, #1d4ed8, #2563eb);
  box-shadow: 0 6px 20px rgba(59, 130, 246, 0.3);
  transform: translateY(-1px);
}

.btn-danger {
  background: linear-gradient(135deg, #dc2626, #ef4444);
  color: #fff;
  border: 1px solid rgba(255, 255, 255, 0.5);
  box-shadow: 0 4px 15px rgba(239, 68, 68, 0.2);
}

.btn-danger:hover {
  background: linear-gradient(135deg, #b91c1c, #dc2626);
  box-shadow: 0 6px 20px rgba(239, 68, 68, 0.3);
}

.btn-ghost {
  background: rgba(255, 255, 255, 0.5);
  color: var(--text-main);
  border: 1px solid var(--glass-border);
}

.btn-ghost:hover {
  background: rgba(255, 255, 255, 0.8);
  border-color: var(--glass-border-light);
}

/* Forms & Inputs */
.field {
  margin-bottom: 20px;
}

label {
  display: block;
  font-size: 0.85rem;
  font-weight: 500;
  color: var(--text-muted);
  margin-bottom: 8px;
}

input[type="text"],
input[type="email"],
input[type="password"],
select,
textarea {
  width: 100%;
  padding: 12px 16px;
  background: rgba(255, 255, 255, 0.6);
  border: 1px solid var(--glass-border);
  border-radius: 12px;
  font-family: inherit;
  font-size: 0.95rem;
  color: var(--text-main);
  transition: all 0.2s ease;
  backdrop-filter: blur(10px);
}

input::placeholder, textarea::placeholder {
  color: var(--text-dim);
}

input:focus, select:focus, textarea:focus {
  outline: none;
  border-color: rgba(59, 130, 246, 0.5);
  background: rgba(255, 255, 255, 0.9);
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.15);
}

select {
  cursor: pointer;
  appearance: none;
  background-image: url("data:image/svg+xml;charset=UTF-8,%3csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%2394a3b8' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3e%3cpolyline points='6 9 12 15 18 9'%3e%3c/polyline%3e%3c/svg%3e");
  background-repeat: no-repeat;
  background-position: right 12px center;
  background-size: 16px;
  padding-right: 40px;
}

/* Flash Messages */
.flash {
  background: rgba(239, 68, 68, 0.15);
  border: 1px solid rgba(239, 68, 68, 0.3);
  color: #dc2626;
  font-size: 0.9rem;
  padding: 12px 16px;
  border-radius: 12px;
  margin-bottom: 20px;
  display: flex;
  align-items: center;
}

/* Tables */
table {
  width: 100%;
  border-collapse: collapse;
}

thead th {
  background: rgba(59, 130, 246, 0.05);
  padding: 16px 20px;
  text-align: left;
  font-size: 0.75rem;
  font-weight: 600;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: var(--text-muted);
  border-bottom: 1px solid var(--glass-border);
}

tbody tr {
  border-bottom: 1px solid rgba(59, 130, 246, 0.05);
  transition: background 0.2s;
}

tbody tr:last-child {
  border-bottom: none;
}

tbody tr:hover {
  background: rgba(59, 130, 246, 0.05);
}

tbody td {
  padding: 16px 20px;
  font-size: 0.95rem;
  color: var(--text-main);
}

/* Specific Utilities */
.sep {
  width: 1px;
  height: 24px;
  background: var(--glass-border);
  margin: 0 8px;
}

/* Scrollbar */
::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}

::-webkit-scrollbar-track {
  background: rgba(255, 255, 255, 0.2);
}

::-webkit-scrollbar-thumb {
  background: rgba(59, 130, 246, 0.15);
  border-radius: 4px;
}

::-webkit-scrollbar-thumb:hover {
  background: rgba(59, 130, 246, 0.25);
}

"""

with open('static/style.css', 'r', encoding='utf-8') as f:
    content = f.read()

parts = content.split('/* ---- INDEX.HTML SPECIFIC ---- */')
if len(parts) > 1:
    specific_css = '/* ---- INDEX.HTML SPECIFIC ---- */' + parts[1]
    
    # Also fix some specific parts in the bottom specific css that still use old variables
    specific_css = specific_css.replace('var(--text)', 'var(--text-main)')
    specific_css = specific_css.replace('var(--muted)', 'var(--text-muted)')
    specific_css = specific_css.replace('var(--dim)', 'var(--text-dim)')
    # ensure .glass logic is not there
    specific_css = re.sub(r'\.glass\s*\{[^}]*\}', '', specific_css, flags=re.DOTALL)
    
    new_css = css_top + specific_css
    
    with open('static/style.css', 'w', encoding='utf-8') as f:
        f.write(new_css)
else:
    print("Could not find specific part in style.css")
