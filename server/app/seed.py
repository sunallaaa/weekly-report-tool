# -*- coding: utf-8 -*-
"""브랜드검색 CPT 계약 일괄 등록 (계약 테이블이 비어 있을 때만).

실행: docker exec <컨테이너> python -m app.seed /app/data/contracts.json
contracts.json 형식 (금액은 VAT 제외 계약 총액):
  [{"advertiser": "mars", "brand": "브랜드", "start": "2026-10-02", "end": "2026-12-30", "amount": 900000, "memo": ""}]
광고주 계약 정보이므로 이 파일은 서버 data 폴더에만 두고 저장소에 올리지 않는다.
"""
import json
import sys
from datetime import date

from sqlalchemy import func, select

from . import db


def main(path):
    items = json.load(open(path, encoding='utf-8'))
    with db.engine().begin() as c:
        if c.execute(select(func.count()).select_from(db.contracts)).scalar():
            print('계약이 이미 있어 건너뜁니다. (수정은 관리 화면에서)')
            return
        for it in items:
            c.execute(db.contracts.insert().values(
                advertiser=it.get('advertiser', 'mars'), brand=it['brand'], start=date.fromisoformat(it['start']),
                end=date.fromisoformat(it['end']), amount=float(it['amount']), memo=it.get('memo', '')))
    print('브랜드검색 계약 {}건 등록'.format(len(items)))


if __name__ == '__main__':
    main(sys.argv[1])
