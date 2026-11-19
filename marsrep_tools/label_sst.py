# -*- coding: utf-8 -*-
"""지정 셀의 라벨 텍스트를 sharedStrings 항목으로 저장 (기존 라벨과 같은 방식 → mailxl 채널 인식)."""
import sys, re, zipfile
sys.path.insert(0, sys.argv[1])
from core.sheetmap import sheet_map
from core.xlsxpatch import Workbook
src, dst, sheet = sys.argv[2:5]; cells = dict(a.split('=', 1) for a in sys.argv[5:])
wb = Workbook(src); sm = sheet_map(wb); sst = wb.text('xl/sharedStrings.xml')
esc = lambda s: s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
items = re.findall(r'<si>.*?</si>', sst, re.S)
for ref, txt in cells.items():
    key = '<si><t>%s</t></si>' % esc(txt)
    if key in items: idx = items.index(key)
    else:
        idx = len(items); items.append(key)
        sst = sst.replace('</sst>', key + '</sst>')
    t = wb.text(sm[sheet]); m = re.search(r'<c r="%s"([^>]*?)(?:/>|>.*?</c>)' % ref, t, re.S)
    sa = re.search(r's="\d+"', m.group(1)); sa = ' ' + sa.group(0) if sa else ''
    wb.set_text(sm[sheet], t[:m.start()] + '<c r="%s"%s t="s"><v>%d</v></c>' % (ref, sa, idx) + t[m.end():])
    print(ref, txt, '→ sst', idx)
n = len(items)
sst = re.sub(r'uniqueCount="\d+"', 'uniqueCount="%d"' % n, sst)
cm = re.search(r' count="(\d+)"', sst)
if cm: sst = sst.replace(cm.group(0), ' count="%d"' % (int(cm.group(1)) + len(cells)), 1)
wb.set_text('xl/sharedStrings.xml', sst); wb.save(dst)
