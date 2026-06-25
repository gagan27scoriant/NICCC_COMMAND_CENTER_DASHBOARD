import sys

with open('templates/dashboard.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Variables
content = content.replace('--bg: #030811;', '--bg: #000000;')
content = content.replace('--panel: rgba(10, 22, 40, 0.6);', '--panel: rgba(20, 20, 20, 0.6);')
content = content.replace('--panel-strong: rgba(12, 28, 50, 0.85);', '--panel-strong: rgba(30, 30, 30, 0.85);')
content = content.replace('--border: rgba(0, 195, 255, 0.15);', '--border: rgba(255, 255, 255, 0.15);')
content = content.replace('--border-strong: rgba(0, 195, 255, 0.3);', '--border-strong: rgba(255, 255, 255, 0.3);')
content = content.replace('--cyan: #00d2ff;', '--cyan: #ffffff;')
content = content.replace('--cyan-dim: rgba(0, 210, 255, 0.08);', '--cyan-dim: rgba(255, 255, 255, 0.08);')
content = content.replace('--teal: #00e5ff;', '--teal: #e0e0e0;')

# Replace RGBA colors
content = content.replace('rgba(0, 220, 255,', 'rgba(255, 255, 255,')
content = content.replace('rgba(0, 210, 255,', 'rgba(255, 255, 255,')
content = content.replace('rgba(0, 255, 195,', 'rgba(200, 200, 200,')
content = content.replace('rgba(0, 180, 220,', 'rgba(200, 200, 200,')
content = content.replace('rgba(0, 200, 240,', 'rgba(220, 220, 220,')
content = content.replace('rgba(0, 229, 255,', 'rgba(255, 255, 255,')

# Replace panel gradients
content = content.replace('rgba(5, 27, 49,', 'rgba(25, 25, 25,')
content = content.replace('rgba(3, 18, 35,', 'rgba(15, 15, 15,')
content = content.replace('rgba(4, 20, 38,', 'rgba(20, 20, 20,')

# Replace hardcoded hex colors
content = content.replace('#00d2ff', '#ffffff')
content = content.replace('#00ffc3', '#e0e0e0')
content = content.replace('#8fffe2', '#cccccc')

# Font change (if not already done)
content = content.replace('family=Inter:wght@400;500;600', 'family=Outfit:wght@300;400;500;600;700')
content = content.replace("--font-ui: 'Inter', sans-serif;", "--font-ui: 'Outfit', sans-serif;")

with open('templates/dashboard.html', 'w', encoding='utf-8') as f:
    f.write(content)

print('Theme updated!')
