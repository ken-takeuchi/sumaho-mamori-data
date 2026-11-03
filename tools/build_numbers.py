"""
総務省「電気通信番号指定状況」から、スマホまもり用の番号データ（numbers.tsv）を作る。

使い方:
  python tools/build_numbers.py                 # 総務省のサイトから取得して作る（GitHub Actions用）
  python tools/build_numbers.py --local DIR     # DIR内の xls/xlsx/pdf から作る（手元での確認用）

出力（numbers.tsv）の形式:
  #version<TAB>指定状況の時点
  #source<TAB>出典
  #carrier<TAB>番号<TAB>会社名
  #area<TAB>番号<TAB>地域名
  番号の頭<TAB>会社の番号<TAB>地域の番号(-1=不明)<TAB>使用中なら1、未使用なら0
"""
import argparse
import io
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd

PAGE_URL = "https://www.soumu.go.jp/main_sosiki/joho_tsusin/top/tel_number/number_shitei.html"
# 「市外局番の一覧」（番号区画コード → 地域名）。ページから見つからない時の予備
AREA_PDF_FALLBACK = "https://www.soumu.go.jp/main_content/000141817.pdf"
SOURCE = "総務省「電気通信番号指定状況」「市外局番の一覧」（CC BY）を加工して作成"

# 会社名を短く分かりやすくする
CARRIER_SHORT = {
    "東日本電信電話": "NTT東日本",
    "西日本電信電話": "NTT西日本",
    "エヌ・ティ・ティ・コミュニケーションズ": "NTTコミュニケーションズ",
    "NTTドコモ": "NTTドコモ",
}


def nfkc(s) -> str:
    return unicodedata.normalize("NFKC", str(s)).strip()


def short_carrier(name: str) -> str:
    n = nfkc(name)
    n = re.sub(r"株式会社|有限会社|合同会社", "", n).strip()
    for k, v in CARRIER_SHORT.items():
        if k in n:
            return v
    return n


def is_empty(v) -> bool:
    return v is None or (isinstance(v, float) and pd.isna(v)) or nfkc(v) in ("", "nan")


# ---------- 取得 ----------

def fetch_sources():
    import requests
    from bs4 import BeautifulSoup

    html = requests.get(PAGE_URL, timeout=60)
    html.encoding = html.apparent_encoding
    soup = BeautifulSoup(html.text, "html.parser")
    tables, area_pdf, version = [], None, ""
    m = re.search(r"(令和\s*\d+\s*年\s*\d+\s*月\s*\d+\s*日)時点", soup.get_text())
    if m:
        version = re.sub(r"\s", "", m.group(1))
    for a in soup.find_all("a", href=True):
        href = requests.compat.urljoin(PAGE_URL, a["href"])
        text = nfkc(a.get_text())
        if re.search(r"\.xlsx?$", href, re.I):
            tables.append((text, href))
        elif href.lower().endswith(".pdf") and "市外局番" in text:
            area_pdf = href
    files = []
    for text, url in tables:
        r = requests.get(url, timeout=120)
        r.raise_for_status()
        files.append((text, url, r.content))
        print(f"取得: {text} ({len(r.content):,} bytes)", file=sys.stderr)
    pdf = requests.get(area_pdf or AREA_PDF_FALLBACK, timeout=120).content
    return files, pdf, version


def local_sources(d: Path):
    files = []
    for p in sorted(d.iterdir()):
        if p.suffix.lower() in (".xls", ".xlsx"):
            files.append((p.name, str(p), p.read_bytes()))
    pdfs = sorted(p for p in d.iterdir() if p.suffix.lower() == ".pdf")
    return files, (pdfs[0].read_bytes() if pdfs else None), "手元のファイル"


# ---------- 変換 ----------

def parse_table(content: bytes, url: str):
    """
    2種類の表に対応する。
    A) 固定電話型: 見出しに「番号」「事業者」（と「使用状況」「番号区画コード」）がある
    B) 携帯電話型: 見出しが「番号, 0, 1, ... 9」で、行の番号＋列の数字が番号の頭になる
    戻り値: [(番号の頭, 会社名 or None, 区画コード or None)]
    """
    out = []
    sheets = pd.read_excel(io.BytesIO(content), sheet_name=None, header=None, dtype=str)
    for _, df in sheets.items():
        rows = df.values.tolist()
        for i, row in enumerate(rows):
            cells = [nfkc(c) if not is_empty(c) else "" for c in row]
            # A) 固定電話型
            if "番号" in cells and "事業者" in cells:
                ci_num, ci_car = cells.index("番号"), cells.index("事業者")
                ci_use = cells.index("使用状況") if "使用状況" in cells else None
                ci_area = cells.index("番号区画コード") if "番号区画コード" in cells else None
                for r in rows[i + 1:]:
                    num = nfkc(r[ci_num]) if not is_empty(r[ci_num]) else ""
                    if not re.fullmatch(r"0\d{3,8}", num):
                        continue
                    car = None if is_empty(r[ci_car]) else nfkc(r[ci_car])
                    if ci_use is not None and not is_empty(r[ci_use]) and "未使用" in nfkc(r[ci_use]):
                        car = None
                    area = None
                    if ci_area is not None and not is_empty(r[ci_area]):
                        area = nfkc(r[ci_area])
                    out.append((num, car, area))
                break
            # B) 携帯電話型
            if cells and cells[0] == "番号" and cells[1:11] == [str(d) for d in range(10)]:
                for r in rows[i + 1:]:
                    head = nfkc(r[0]) if not is_empty(r[0]) else ""
                    if not re.fullmatch(r"0\d{2,8}", head):
                        continue
                    for d in range(10):
                        v = r[d + 1] if d + 1 < len(r) else None
                        out.append((head + str(d), None if is_empty(v) else nfkc(v), None))
                break
    if not out:
        print(f"  形式が分からないため読み飛ばし: {url}", file=sys.stderr)
    return out


def norm_area_code(code: str) -> str:
    """'001' や '4-2' をそろえる（先頭の0を取る）"""
    m = re.fullmatch(r"0*(\d+)(-\d+)?", nfkc(code))
    return (m.group(1) + (m.group(2) or "")) if m else nfkc(code)


def parse_area_pdf(pdf: bytes):
    """市外局番の一覧PDFから、番号区画コード → 短い地域名（例：北海道江別市）"""
    if not pdf:
        return {}
    import pdfplumber

    text = ""
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        for page in doc.pages:
            text += (page.extract_text() or "") + "\n"
    text = nfkc(text)
    areas = {}
    # 「コード 地域名（複数行のことあり） 市外局番 市内局番の桁」を拾う
    pat = re.compile(
        r"(?m)^(\d+(?:-\d+)?)\s+(.+?)\s+(\d{1,4})\s+(BCDE|CDE|DE|E)\s*$", re.S,
    )
    for m in pat.finditer(text):
        region = re.sub(r"\s+", "", m.group(2))
        region = re.sub(r"[(（].*?[)）]", "", region)  # 括弧の注記を外す
        first = region.split("、")[0]
        if 2 <= len(first) <= 30:
            areas[norm_area_code(m.group(1))] = first
    print(f"地域名: {len(areas)}件", file=sys.stderr)
    return areas


def build(files, pdf, version):
    areas_by_code = parse_area_pdf(pdf)
    carriers, area_names = [], []
    rows = {}
    for title, url, content in files:
        try:
            entries = parse_table(content, url)
        except Exception as e:  # 番号以外のファイル（IMSIなど）は読めなくてよい
            print(f"  読み込み失敗のため読み飛ばし: {title} ({e})", file=sys.stderr)
            continue
        for num, car, area in entries:
            ci = -1
            if car:
                name = short_carrier(car)
                if name not in carriers:
                    carriers.append(name)
                ci = carriers.index(name)
            ai = -1
            if area:
                name = areas_by_code.get(norm_area_code(area))
                if name:
                    if name not in area_names:
                        area_names.append(name)
                    ai = area_names.index(name)
            rows[num] = (ci, ai, 1 if car else 0)
    lines = [f"#version\t{version}", f"#source\t{SOURCE}"]
    lines += [f"#carrier\t{i}\t{n}" for i, n in enumerate(carriers)]
    lines += [f"#area\t{i}\t{n}" for i, n in enumerate(area_names)]
    lines += [f"{k}\t{c}\t{a}\t{u}" for k, (c, a, u) in sorted(rows.items())]
    return "\n".join(lines) + "\n", len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", type=Path)
    ap.add_argument("--out", type=Path, default=Path("numbers.tsv"))
    args = ap.parse_args()
    files, pdf, version = local_sources(args.local) if args.local else fetch_sources()
    tsv, n = build(files, pdf, version)
    if n < 1000 and not args.local:
        sys.exit(f"番号が少なすぎます（{n}件）。総務省のページの形式が変わった可能性があります。")
    args.out.write_text(tsv, encoding="utf-8")
    print(f"書き出し: {args.out}（{n:,}件, {len(tsv.encode()):,} bytes）", file=sys.stderr)


if __name__ == "__main__":
    main()
