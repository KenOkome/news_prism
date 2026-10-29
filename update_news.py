# -*- coding: utf-8 -*-
"""
NewsPrism - 毎朝のニュース自動更新＆3軸ダイナミックスケーリングスクリプト
1. 多彩なジャンル（国内、国際、経済、科学技術、オピニオン、エンタメ、生活）からニュースを収集
2. 3軸（赤：興味、緑：思想近さ、青：客観事実度）のスコアリングを大幅に多様化
3. ニュース全体の平均・標準偏差を基に、2D空間（4象限）全体へ美しく分散するように正規化（ダイナミックスケーリング）
"""

import os
import sys
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data", "news")
os.makedirs(DATA_DIR, exist_ok=True)

# 4象限（興味の高低、思想の遠近、事実の硬軟）がバランスよく集まるカテゴリ一覧
RSS_SOURCES = [
    ("テクノロジー・科学", "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=ja&gl=JP&ceid=JP:ja"),
    ("経済・ビジネス", "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=ja&gl=JP&ceid=JP:ja"),
    ("政治・社会", "https://news.google.com/rss/headlines/section/topic/NATION?hl=ja&gl=JP&ceid=JP:ja"),
    ("国際情勢", "https://news.google.com/rss/headlines/section/topic/WORLD?hl=ja&gl=JP&ceid=JP:ja"),
    ("文化・エンタメ", "https://news.google.com/rss/headlines/section/topic/ENTERTAINMENT?hl=ja&gl=JP&ceid=JP:ja"),
    ("スポーツ", "https://news.google.com/rss/headlines/section/topic/SPORTS?hl=ja&gl=JP&ceid=JP:ja")
]

# 煽情的な炎上・煽り表現（Ragebait検知）
SENSATIONAL_PATTERNS = [
    r"ブチギレ", r"大炎上", r"悲報", r"衝撃", r"ヤバす", r"崩壊", r"屈辱",
    r"怒号", r"呆れた", r"惨敗", r"末路", r"愚行", r"売国", r"断末魔", r"[!?！？]{2,}"
]

# 事実接地（数値・公的機関・一次引用）の検知パターン
GROUNDING_PATTERNS = [
    r"\d+(?:\.\d+)?(?:%|％|万|億|兆|円|人|件|社|ポイント)",
    r"(?:共同通信|時事通信|ロイター|ブルームバーグ|統計局|省|庁|警察署|裁判所|研究所|大学)に(?:よると|よれば|よる)",
    r"「[^」]{10,}」",
    r"(?:調査|統計|論文|白書|決算|判決|議事録|年次報告)に(?:よると|基づき|よれば)"
]

def calculate_sensationalism(text):
    """煽り・感情誘導度（0.0 〜 1.0）"""
    hit_count = sum(1 for p in SENSATIONAL_PATTERNS if re.search(p, text))
    return min(1.0, hit_count * 0.45)

def calculate_grounding(text):
    """事実接地・論拠密度（0.0 〜 1.0）"""
    hit_count = sum(1 for p in GROUNDING_PATTERNS if re.search(p, text))
    return min(1.0, 0.2 + hit_count * 0.25)

def clean_html(text):
    if not text:
        return ""
    return re.sub(r'<[^>]+>', '', text).strip()

def analyze_article_raw(title, summary, source, category):
    """
    記事の内容から未正規化の生スコアを算出（良質異論ゲート付き）
    """
    text = (title + " " + summary).lower()

    # カテゴリ基準値
    cat_defaults = {
        "テクノロジー・科学": {"r": 220, "g": 160, "b": 210},
        "経済・ビジネス": {"r": 180, "g": 120, "b": 170},
        "政治・社会": {"r": 160, "g": 110, "b": 150},
        "国際情勢": {"r": 140, "g": 100, "b": 160},
        "文化・エンタメ": {"r": 60, "g": 140, "b": 90},
        "スポーツ": {"r": 50, "g": 150, "b": 140}
    }
    base = cat_defaults.get(category, {"r": 120, "g": 120, "b": 140})
    r, g, b = base["r"], base["g"], base["b"]

    sensationalism = calculate_sensationalism(text)
    grounding = calculate_grounding(text)

    # 1. 青：客観事実度（B）
    opinion_patterns = ["社説", "コラム", "提言", "主張", "見解", "論評", "批判", "どう見る", "懸念", "思惑", "危機感", "波紋", "なぜ", "問題点", "疑問", "本質"]
    fact_patterns = ["発表", "実証", "データ", "調査", "統計", "実験", "公表", "可決", "成立", "観測", "数値", "確認", "条文", "ガイドライン", "逮捕", "起訴", "判決", "合意"]
    for w in opinion_patterns:
        if w in text: b -= 35
    for w in fact_patterns:
        if w in text: b += 30

    # 煽り度が高い場合は事実度をペナルティ減算、論拠が高い場合は加算
    if sensationalism > 0.3:
        b -= int(sensationalism * 40)
    if grounding > 0.4:
        b += int(grounding * 30)

    # 2. 赤：興味・関心度（R）
    high_interest = ["ai", "人工知能", "テクノロジー", "科学", "半導体", "電池", "エネルギー", "水素", "宇宙", "教育", "学校", "プログラミング", "働き方", "制度", "新技術", "自動化", "防衛", "憲法", "選挙", "経済", "インフレ", "金融"]
    low_interest = ["芸能", "熱愛", "破局", "タレント", "アイドル", "不倫", "結婚発表", "写真集", "プロ野球", "サッカー", "大谷", "優勝", "得点"]
    for w in high_interest:
        if w in text: r += 25
    for w in low_interest:
        if w in text: r -= 40

    # 3. 緑：思想・スタンス近さ（G）
    aligned_patterns = ["共生", "保護", "支援", "公正", "透明性", "対話", "検証", "セーフティネット", "安定", "事実確認", "改善", "持続可能", "福祉", "是正", "権利"]
    divergent_patterns = ["削減", "廃止", "撤廃", "解雇", "自己責任", "淘汰", "打ち切り", "強硬", "軍備倍増", "右派", "タカ派", "制限", "規制緩和断行", "打ち切り", "拒絶", "排除", "義務化"]
    for w in aligned_patterns:
        if w in text: g += 35
    for w in divergent_patterns:
        if w in text: g -= 45

    # 良質な異論（Constructive Dissent）の選別
    # 煽り度が低く（<= 0.3）、かつ論拠密度が高い（>= 0.4）場合のみ「良質異論」の資格あり
    is_constructive_dissent = bool(sensationalism <= 0.35 and grounding >= 0.4 and g <= 130)

    return r, g, b, is_constructive_dissent, round(sensationalism, 2), round(grounding, 2)

def normalize_and_spread(articles):
    """
    ニュース全体のスコア分布を分析し、
    R（縦軸：興味）とG（横軸：思想近さ）が四方八方（左上・右上・左下・右下）に綺麗に散らばるようダイナミック展開
    """
    if not articles:
        return articles

    r_vals = [a["r"] for a in articles]
    g_vals = [a["g"] for a in articles]
    b_vals = [a["b"] for a in articles]

    min_r, max_r = min(r_vals), max(r_vals)
    min_g, max_g = min(g_vals), max(g_vals)
    min_b, max_b = min(b_vals), max(b_vals)

    # 範囲が狭い場合のガード
    range_r = max(1, max_r - min_r)
    range_g = max(1, max_g - min_g)
    range_b = max(1, max_b - min_b)

    for a in articles:
        # 30 〜 240 のダイナミックレンジにスケーリング
        norm_r = int(35 + ((a["r"] - min_r) / range_r) * 205)
        norm_g = int(35 + ((a["g"] - min_g) / range_g) * 205)
        norm_b = int(30 + ((a["b"] - min_b) / range_b) * 210)

        a["r"] = max(20, min(245, norm_r))
        a["g"] = max(20, min(245, norm_g))
        a["b"] = max(20, min(245, norm_b))

        # クッション判定の更新
        if a["g"] < 90 and a["b"] < 110:
            a["cushion"] = "【刺激的異論（マゼンタ）】あなたの志向とは真っ向から対立する強いオピニオンです。感情的に反発せず、「反対派が何を根拠に論じているか」という構造に着目してください。"
        elif a["g"] < 90 and a["b"] >= 160:
            a["cushion"] = "【冷静な反証データ（シアン寄りの異論）】あなたの意見とは異なる立場を補強する客観的データ・事実です。自説を客観的に再検証する良質な材料になります。"
        elif a["r"] > 180 and a["g"] > 180 and a["b"] > 170:
            a["cushion"] = "【完全調和（白）】関心度・共感度・客観データ度のすべてが揃った、きわめて有益で安心感のあるレポートです。"
        elif a["r"] > 170 and a["g"] > 170 and a["b"] < 100:
            a["cushion"] = "【心地よい共感（イエロー）】あなたの価値観と深く調和する論評です。納得感は高いですが、客観的な裏付け数字（青）が十分か意識しながらお読みください。"
        elif a["r"] < 90 and a["b"] >= 160:
            a["cushion"] = "【未知の客観事実（シアン）】普段あまり見ない分野の純粋な一次データや公的発表です。視野と知識の幅を広げるのに適しています。"
        elif a["r"] < 90 and a["g"] < 90:
            a["cushion"] = "【普段スルーする深淵】個人的関心も思想的親和性も低い分野ですが、世の中の多様な視座を知るためのサンプリング記事です。"
        else:
            a["cushion"] = "客観的な事実関係が整理されたニュースです。前提知識として冷静に把握するのに適しています。"

    return articles

def fetch_latest_news():
    articles = []
    seen_titles = set()

    for category, url in RSS_SOURCES:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            content = urllib.request.urlopen(req, timeout=8).read()
            root = ET.fromstring(content)
            items = root.findall('.//item')

            cat_count = 0
            for item in items:
                raw_title = item.find('title').text or ""
                parts = raw_title.rsplit(" - ", 1)
                title = parts[0].strip()
                source = parts[1].strip() if len(parts) > 1 else (item.find('source').text if item.find('source') is not None else category)

                if title in seen_titles or len(title) < 10:
                    continue
                seen_titles.add(title)

                link = item.find('link').text if item.find('link') is not None else ""
                desc = clean_html(item.find('description').text if item.find('description') is not None else "")
                if not desc or len(desc) < 20:
                    desc = title

                r, g, b, is_constructive, sens, grnd = analyze_article_raw(title, desc, source, category)

                articles.append({
                    "id": len(articles) + 1,
                    "title": title,
                    "source": f"{source} / {category}",
                    "link": link,
                    "r": r,
                    "g": g,
                    "b": b,
                    "is_constructive": is_constructive,
                    "sensationalism": sens,
                    "grounding": grnd,
                    "summary": desc[:160] + ("..." if len(desc) > 160 else ""),
                    "cushion": "",
                    "body": f"【報道概要】\n{desc}\n\n【情報ソース】{source} ({category})\n【元記事リンク】{link}"
                })

                cat_count += 1
                if cat_count >= 3: # 1ジャンルあたり上位3件を厳選して多角的に取得
                    break
        except Exception as e:
            print(f"Error fetching {category}: {e}", file=sys.stderr)

    # 4象限へのダイナミックスケーリング（分散化・是正）
    articles = normalize_and_spread(articles)
    return articles

def main():
    today_str = datetime.now().strftime("%Y-%m-%d")
    print(f"[{datetime.now()}] Updating & rebalancing news for {today_str}...")

    articles = fetch_latest_news()
    if not articles:
        print("No articles fetched, aborting.")
        return

    today_file = os.path.join(DATA_DIR, f"{today_str}.json")
    with open(today_file, "w", encoding="utf-8") as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(articles)} articles to {today_file}")

    all_files = sorted([f.replace(".json", "") for f in os.listdir(DATA_DIR) if f.endswith(".json") and re.match(r'^\d{4}-\d{2}-\d{2}$', f.replace(".json", ""))], reverse=True)
    dates_file = os.path.join(DATA_DIR, "dates.json")
    with open(dates_file, "w", encoding="utf-8") as f:
        json.dump(all_files, f, ensure_ascii=False, indent=2)

    js_content = f"// 自動生成されたニュースデータ ({today_str})\n"
    js_content += f"window.AVAILABLE_DATES = {json.dumps(all_files, ensure_ascii=False)};\n"
    js_content += f"window.CURRENT_DATE = '{today_str}';\n"
    js_content += f"window.NEWS_DATA_TODAY = {json.dumps(articles, ensure_ascii=False, indent=2)};\n"

    js_file = os.path.join(BASE_DIR, "news_data.js")
    with open(js_file, "w", encoding="utf-8") as f:
        f.write(js_content)
    print(f"Updated {js_file} successfully.")

if __name__ == "__main__":
    main()
