#!/usr/bin/env python3
"""EMPIRE 핫딜관 주문서 자동 갱신.

애드픽 핫딜 API(공개 상품 피드, 회원 코드만 필요)를 4시간마다(하루 6번) 불러와서
주문서/핫딜.csv 를 다시 쓴다. 실패하거나 빈 응답이면 기존 파일을 그대로 둔다.

애드픽 규칙: 1분에 1회 이하 호출, 받아 온 결과는 저장해서 재사용.
→ 깃허브 액션이 하루 6번 돌리고(회차마다 65초 간격 6회 호출), 방문자는 이 CSV만 읽는다.
"""
import csv
import io
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

AFFID = os.environ.get("ADPICK_AFFID", "ee254f")
API = f"https://adpick.co.kr/apis/sdk_shopping_hotdeal.php?affid={AFFID}&viewlist=30"
OUT = os.path.join(os.path.dirname(__file__), "..", "주문서", "핫딜.csv")
MAX_ITEMS = 40
CALLS = int(os.environ.get("HOTDEAL_CALLS", "6"))   # 애드픽 규칙: 1분에 1번 이하 → 호출 사이 65초 쉼
GAP_SEC = 65
PRIORITY_MALLS = ("필웨이",)   # 명품: 최대한 많이, 맨 앞에


def clean(text, limit=80):
    text = re.sub(r"[\x00-\x1f\x7f]", " ", str(text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def https_only(url):
    url = str(url or "").strip()
    return url if url.lower().startswith("https://") else ""


def to_int(price):
    digits = re.sub(r"[^0-9]", "", str(price or ""))
    return int(digits) if digits else 0


def fetch():
    req = urllib.request.Request(API, headers={"User-Agent": "EMPIRE-hotdeal/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def build_rows(themes_list):
    """여러 번 받아 온 피드를 합치고, 필웨이(명품)를 맨 앞에, 그다음 할인율 높은 순."""
    items = []
    seen = set()
    for data in themes_list:
        for theme in data if isinstance(data, list) else []:
            for it in theme.get("list", []) or []:
                name = clean(it.get("product_name"), 70)
                link = https_only(it.get("buyurl"))
                # 내 회원 코드로 잡히는 애드픽 링크만 받는다
                if not name or not link.startswith("https://adpick.co.kr/") or f"affid={AFFID}" not in link:
                    continue
                if link in seen:
                    continue
                seen.add(link)
                mall = clean(it.get("mall_name"), 20) or "특가"
                # 가격은 애드픽이 준 숫자를 그대로 쓴다
                sale_txt = clean(it.get("price_sale"), 20)
                org_txt = clean(it.get("price_org"), 20)
                sale, org_v = to_int(sale_txt), to_int(org_txt)
                if 0 < sale < 100:  # 2026-11-21: '4원' 같은 피드 오류 가격은 손님을 속이게 되니 뺀다
                    continue
                price = f"{sale_txt}원" if sale else ""
                org = f"{org_txt}원" if org_v > sale > 0 else ""
                rate = (org_v - sale) / org_v if org else 0.0
                img = https_only(it.get("photo"))
                items.append({"mall": mall, "sale": sale, "rate": rate,
                              "row": [mall, name, price, link, mall, img, org]})
    return arrange(items)


LUXURY_MALL = "필웨이"    # 맨 위 명품 진열: 고가 2 + 중가 2 (2026-11-21 대표)
BARGAIN_MALL = "이유몰"   # 그 밑: 땡처리 먹거리·생필품 전부
LUXURY_TOP = "★ 명품 진열 · 필웨이"
BARGAIN_TOP = "★ 이유몰 땡처리 · 먹거리·생필품"


def arrange(items):
    """4시간마다 새로 고른다 → 품절·가격 바뀐 상품이 안 남는다.
    ① 필웨이 고가 2 + 중가 2(가격 가운데쯤) ② 이유몰 전부 ③ 나머지 할인율 순. 카테고리가 ★로 시작하면 화면 맨 위."""
    by_rate = lambda x: -x["rate"]
    lux = sorted([x for x in items if x["mall"] == LUXURY_MALL and x["sale"] > 0], key=lambda x: -x["sale"])
    picked = lux[:2]
    rest_lux = lux[2:]
    if rest_lux:
        mid = len(rest_lux) // 2
        picked += rest_lux[max(0, mid - 1):mid + 1]
    picked_ids = {id(x) for x in picked}
    bargain = sorted([x for x in items if x["mall"] == BARGAIN_MALL], key=by_rate)
    others = sorted([x for x in items if id(x) not in picked_ids and x["mall"] != BARGAIN_MALL], key=by_rate)
    out = []
    for x in picked:
        out.append([LUXURY_TOP] + x["row"][1:])
    for x in bargain:
        out.append([BARGAIN_TOP] + x["row"][1:])
    out += [x["row"] for x in others]
    return out[:MAX_ITEMS]


def fetch_many():
    got = []
    for i in range(CALLS):
        if i:
            time.sleep(GAP_SEC)
        try:
            got.append(fetch())
        except Exception as e:
            print(f"{i + 1}번째 호출 실패: {e}")
    return got


def main():
    batches = fetch_many()
    if not batches:
        print("API 실패, 기존 주문서 유지 — 실패(1)로 끝내 GitHub 알림")
        return 1
    rows = build_rows(batches)
    if not rows:
        print("받아 온 상품이 없어서 기존 주문서 유지 — 실패(1)로 끝내 GitHub 알림")
        return 1
    kst = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")
    buf = io.StringIO()
    buf.write("# 핫딜관 주문서 — 애드픽 핫딜 API에서 4시간마다 자동으로 채워져요. 손으로 고치지 마세요.\n")
    buf.write(f"# 마지막 갱신: {kst} (한국 시간) · 상품 {len(rows)}개\n")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["카테고리", "상품명", "가격", "링크", "한줄설명", "이미지", "원가"])
    w.writerows(rows)
    new = buf.getvalue()
    old = open(OUT, encoding="utf-8").read() if os.path.exists(OUT) else ""
    strip = lambda s: "\n".join(l for l in s.splitlines() if not l.startswith("# 마지막 갱신"))
    if strip(old) == strip(new):
        print("상품이 그대로라 갱신 안 함")
        return 0
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(new)
    print(f"핫딜 {len(rows)}개로 갱신")
    return 0


if __name__ == "__main__":
    sys.exit(main())
