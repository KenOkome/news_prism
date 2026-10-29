# -*- coding: utf-8 -*-
"""
NewsPrism - 毎朝のニュース自動更新＆3軸スコアリングスクリプト
1. Google News RSS等から最新の時事ニュースを取得
2. 各記事を3軸（赤：興味、緑：思想近さ、青：客観事実度）で分析・スコアリング
3. data/news/YYYY-MM-DD.json に保存し、最新データ用の news_data.js および dates.json を更新
"""

import os
import sys
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

# スクリプトの配置ディレクトリ
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data", "news")
os.makedirs(DATA_DIR, exist_ok=True)

# ニュースRSS URLリスト（総合、経済、テクノロジー、国際、政治）
RSS_URLS = [
    "https://news.google.com/rss?hl=ja&gl=JP&ceid=JP:ja",
    "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=ja&gl=JP&ceid=JP:ja",
    "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=ja&gl=JP&ceid=JP:ja"
]

def clean_html(text):
    if not text:
        return ""
    return re.sub(r'<[^>]+>', '', text).strip()

def analyze_article(title, summary, source):
    """
    記事のタイトルや概要から、3軸スコア（0-255）とAIクッションをルール＋意味解析で推定
    R: 興味・関心度（テクノロジー、AI、科学、教育、経済改革、ガジェット等は高め）
    G: 思想・スタンス近さ（雇用安定、弱者保護、知性・共生重視は高め、極端な市場原理・排外・過激論は低め）
    B: 客観事実度（統計、実証、発表、数値、条文は高め、社説、コラム、提言、オピニオンは低め）
    """
    text = (title + " " + summary).lower()

    # デフォルト中央値
    r = 140
    g = 140
    b = 150

    # 1. 青：客観事実度（B）の判定
    opinion_words = ["社説", "コラム", "提言", "主張", "見解", "論評", "批判", "どう見る", "懸念", "思惑", "危機感", "波紋"]
    fact_words = ["発表", "実証", "データ", "調査", "統計", "実験", "公表", "可決", "成立", "観測", "数値", "確認", "条文", "ガイドライン", "逮捕", "起訴"]

    for w in opinion_words:
        if w in text:
            b -= 25
    for w in fact_words:
        if w in text:
            b += 20
    b = max(30, min(250, b))

    # 2. 赤：興味・関心度（R）の判定（ユーザーの好むテクノロジー、AI、科学、教育、新エネルギー、働き方など）
    high_interest_words = ["ai", "人工知能", "テクノロジー", "科学", "半導体", "電池", "エネルギー", "水素", "宇宙", "教育", "学校", "プログラミング", "働き方", "制度", "新技術", "自動化"]
    low_interest_words = ["芸能", "熱愛", "破局", "タレント", "アイドル", "プロ野球", "サッカー", "大谷", "不倫"]

    for w in high_interest_words:
        if w in text:
            r += 30
    for w in low_interest_words:
        if w in text:
            r -= 35
    r = max(40, min(250, r))

    # 3. 緑：思想・スタンス近さ（G）の判定（生活重視、公正性、冷静な対話は高め、過度な分断や急進・極端な市場主義は遠め）
    # 政治・社会論点での振れ幅
    aligned_words = ["共生", "保護", "支援", "公正", "透明性", "対話", "検証", "セーフティネット", "安定", "事実確認", "改善"]
    divergent_words = ["一斉解雇", "削減断行", "規制撤廃", "自己責任", "淘汰", "打ち切り", "極右", "強硬", "軍備倍増"]

    for w in aligned_words:
        if w in text:
            g += 20
    for w in divergent_words:
        if w in text:
            g -= 30
    g = max(30, min(245, g))

    # クッションコメント生成
    if g < 80 and b < 100:
        cushion = "【刺激的異論】あなたの重視する価値観（安定・保護・共生）とは異なる強い主張を含むオピニオンです。感情的に反発せず、「どのような論理で主張されているか」という構造に着目してください。"
    elif g < 90 and b >= 150:
        cushion = "【冷静な反対材料】あなたの志向とは異なる立場を補強する客観的なデータ・事実です。自説を客観的に再検証する良質な反証材料になります。"
    elif r > 180 and g > 180 and b > 180:
        cushion = "【完全調和（白）】あなたの関心分野かつ価値観に合い、客観的な事実データが揃ったきわめて有益なレポートです。"
    elif b < 100:
        cushion = "共感しやすい論評ですが、客観的な数字や一次データ（青）は少なめです。筆者の視点・解釈としてお読みください。"
    else:
        cushion = "客観的な事実関係が整理されたニュースです。前提知識として冷静に把握するのに適しています。"

    return r, g, b, cushion

def fetch_latest_news():
    articles = []
    seen_titles = set()

    for url in RSS_URLS:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            content = urllib.request.urlopen(req, timeout=10).read()
            root = ET.fromstring(content)
            items = root.findall('.//item')

            for item in items:
                raw_title = item.find('title').text or ""
                # "タイトル - メディア名" の分離
                parts = raw_title.rsplit(" - ", 1)
                title = parts[0].strip()
                source = parts[1].strip() if len(parts) > 1 else (item.find('source').text if item.find('source') is not None else "時事通信")

                if title in seen_titles or len(title) < 10:
                    continue
                seen_titles.add(title)

                link = item.find('link').text if item.find('link') is not None else ""
                desc = clean_html(item.find('description').text if item.find('description') is not None else "")
                if not desc or len(desc) < 20:
                    desc = title

                r, g, b, cushion = analyze_article(title, desc, source)

                articles.append({
                    "id": len(articles) + 1,
                    "title": title,
                    "source": source,
                    "link": link,
                    "r": r,
                    "g": g,
                    "b": b,
                    "summary": desc[:160] + ("..." if len(desc) > 160 else ""),
                    "cushion": cushion,
                    "body": f"【報道概要】\n{desc}\n\n【情報ソース】{source}\n【元記事リンク】{link}"
                })

                if len(articles) >= 15:
                    break
        except Exception as e:
            print(f"Error fetching {url}: {e}", file=sys.stderr)

        if len(articles) >= 15:
            break

    return articles

def generate_sample_past_dates(current_date_str):
    """過去のサンプル日付データを初期生成（初回のみ）"""
    base_date = datetime.strptime(current_date_str, "%Y-%m-%d")
    for i in range(1, 5):
        past_date = (base_date - timedelta(days=i)).strftime("%Y-%m-%d")
        past_file = os.path.join(DATA_DIR, f"{past_date}.json")
        if not os.path.exists(past_file):
            # サンプル記事を少し揺らして作成
            sample_items = [
                {
                    "id": 1,
                    "title": f"【{past_date}の重要ニュース】新世代半導体コンソーシアムの量産進捗レポート",
                    "source": "日経ビジネス",
                    "link": "https://example.com",
                    "r": 235, "g": 210, "b": 240,
                    "summary": "国内主要企業が連携した次世代チップ製造プロジェクトが第2フェーズの実装試験に入ったと公表。",
                    "cushion": "関心度・客観事実度ともに高い技術レポートです。",
                    "body": "工程別の歩留まりデータと今後のスケジュールが発表されました。"
                },
                {
                    "id": 2,
                    "title": f"【{past_date}の論評】最低賃金の一律引き上げが中小零細に及ぼす影響と反論",
                    "source": "経済論壇",
                    "link": "https://example.com",
                    "r": 220, "g": 45, "b": 80,
                    "summary": "コスト転嫁ができない町工場を直撃するとし、地方創生に逆行すると警鐘を鳴らすオピニオン。",
                    "cushion": "【刺激的異論】雇用主目線での厳しい問題提起です。多角的な検討材料としてご覧ください。",
                    "body": "地域別格差と倒産件数の相関についてのコラムです。"
                },
                {
                    "id": 3,
                    "title": f"【{past_date}の行政発表】公立学校における生成AI活用ガイドラインの改定点",
                    "source": "文部科学省 / 教育通信",
                    "link": "https://example.com",
                    "r": 215, "g": 225, "b": 235,
                    "summary": "児童生徒の情報モラル育成と教員の校務効率化を目的とした新基準の全文が告示された。",
                    "cushion": "教育・AIの両面で関心が高く、100%客観的な公的データです。",
                    "body": "利用制限の緩和と学習効果の検証基準について詳しくまとめられています。"
                }
            ]
            with open(past_file, "w", encoding="utf-8") as f:
                json.dump(sample_items, f, ensure_ascii=False, indent=2)

def main():
    today_str = datetime.now().strftime("%Y-%m-%d")
    print(f"[{datetime.now()}] Updating news for {today_str}...")

    # 1. 最新ニュースの収集・スコアリング
    articles = fetch_latest_news()
    if not articles:
        print("No articles fetched, aborting.")
        return

    # 2. 当日分 JSON の保存 (data/news/YYYY-MM-DD.json)
    today_file = os.path.join(DATA_DIR, f"{today_str}.json")
    with open(today_file, "w", encoding="utf-8") as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(articles)} articles to {today_file}")

    # 初回用：過去数日分のサンプルデータも補完
    generate_sample_past_dates(today_str)

    # 3. 利用可能な日付一覧 (dates.json) の更新
    all_files = sorted([f.replace(".json", "") for f in os.listdir(DATA_DIR) if f.endswith(".json") and re.match(r'^\d{4}-\d{2}-\d{2}$', f.replace(".json", ""))], reverse=True)
    dates_file = os.path.join(DATA_DIR, "dates.json")
    with open(dates_file, "w", encoding="utf-8") as f:
        json.dump(all_files, f, ensure_ascii=False, indent=2)
    print(f"Updated available dates: {all_files}")

    # 4. フロントエンド直読用 news_data.js の出力（CORS回避＆即時表示のため）
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
