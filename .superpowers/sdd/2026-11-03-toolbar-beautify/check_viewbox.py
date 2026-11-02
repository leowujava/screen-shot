import os, re
d = os.path.join('assets', 'icons')
for f in sorted(os.listdir(d)):
    if not f.endswith('.svg'):
        continue
    raw = open(os.path.join(d, f), 'rb').read()
    vb = re.search(rb'viewBox="([^"]+)"', raw).group(1).decode()
    print(f, 'viewBox=' + vb, 'newlines=', raw.count(b'\n'))
