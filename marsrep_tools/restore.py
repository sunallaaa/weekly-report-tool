import sys, re, os
out = sys.argv[1]; n = 0
for doc in sys.argv[2:]:
    L = open(doc, encoding='utf-8').read().split('\n')
    heads = [(i, re.match(r'^## 파일: `([^`]+)`\s+\((\d+)줄\)', l)) for i, l in enumerate(L)]
    heads = [(i, m) for i, m in heads if m]
    for k, (i, m) in enumerate(heads):
        end = heads[k + 1][0] if k + 1 < len(heads) else len(L)
        seg = L[i + 1:end]
        s = next(x for x, l in enumerate(seg) if l.startswith('```'))
        e = max(x for x, l in enumerate(seg) if l.strip() == '```')
        body = seg[s + 1:e]
        path, cnt = m.group(1), int(m.group(2))
        p = os.path.join(out, path); os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, 'w', encoding='utf-8').write('\n'.join(body) + '\n'); n += 1
        print(f'{len(body):>4} (선언 {cnt:>4})  {path}')
open(os.path.join(out, 'marsrep/core/__init__.py'), 'a').close()
print('files', n)
