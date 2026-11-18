# -*- coding: utf-8 -*-
"""시범/주간 실행 전처리
1) 명칭 치환 (프로세스 기준 §7): sharedStrings · RAW_all · DA_index 의 '쇼핑프로모션'→'쇼핑고객확장'
   (영문 shppingpromotion 은 다른 문자열이라 영향 없음). 나머지 zip 엔트리는 바이트 그대로 복사.
2) Mars_Daily 단일 파일 → Media 별 2개 xlsx (캠페인·그룹 명칭 치환 적용)
"""
import sys, re, zipfile, openpyxl
OLD, NEW = '쇼핑프로모션', '쇼핑고객확장'

def sheet_paths(z, names):
    wb = z.read('xl/workbook.xml').decode('utf-8')
    rels = z.read('xl/_rels/workbook.xml.rels').decode('utf-8')
    rid = {m.group(2): m.group(1) for m in re.finditer(r'<Relationship [^>]*Id="([^"]+)"[^>]*Target="([^"]+)"', rels)}
    rid = {v: k for k, v in rid.items()}  # Id -> Target
    rid2 = {}
    for m in re.finditer(r'<Relationship ([^>]*)/?>', rels):
        a = m.group(1); i = re.search(r'Id="([^"]+)"', a).group(1); t = re.search(r'Target="([^"]+)"', a).group(1)
        rid2[i] = t if t.startswith('xl/') else 'xl/' + t.lstrip('/')
    out = {}
    for m in re.finditer(r'<sheet [^>]*?name="([^"]+)"[^>]*?r:id="([^"]+)"', wb):
        n = m.group(1).replace('&amp;', '&')
        if n in names: out[rid2[m.group(2)]] = n
    return out

def rename(src, dst):
    zi = zipfile.ZipFile(src)
    targets = {'xl/sharedStrings.xml': 'sharedStrings'}
    targets.update(sheet_paths(zi, {'RAW_all', 'DA_index'}))
    zo = zipfile.ZipFile(dst, 'w')
    cnt = {}
    for info in zi.infolist():
        data = zi.read(info.filename)
        if info.filename in targets:
            s = data.decode('utf-8'); c = s.count(OLD)
            data = s.replace(OLD, NEW).encode('utf-8'); cnt[targets[info.filename]] = c
        zo.writestr(info, data)
    zo.close()
    return cnt

def split_daily(src, out_gfa, out_bs):
    wb = openpyxl.load_workbook(src, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = [r[:10] for r in ws.iter_rows(values_only=True) if r and r[0] is not None]
    head, body = rows[0], rows[1:]
    res = {}
    for media, path in (('GFA', out_gfa), ('NaverBS', out_bs)):
        o = openpyxl.Workbook(); s = o.active; s.title = 'Msrs Daily'; s.append(list(head)); n = 0
        for r in body:
            if r[1] != media: continue
            r = list(r)
            for i in (3, 4):
                if isinstance(r[i], str): r[i] = r[i].replace(OLD, NEW)
            s.append(r); n += 1
        o.save(path); res[media] = n
    return res

if __name__ == '__main__':
    base, daily, outdir = sys.argv[1:4]
    print('명칭 치환', rename(base, outdir + '/base_renamed.xlsx'))
    print('로우 분리', split_daily(daily, outdir + '/gfa.xlsx', outdir + '/bs.xlsx'))
