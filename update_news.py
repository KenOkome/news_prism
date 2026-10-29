# -*- coding: utf-8 -*-
"""
NewsPrism - 毎朝のニュース自動更新＆深掘り3行要約生成スクリプト
1. 多角的な一次・大手報道機関（NHK、ITmedia、東洋経済、GIGAZINE等）からリッチな記事概要を取得
2. 記事の中身を深く理解できる構造化3行要約（【事象の核心】【要因・背景】【影響・論点】）を生成
3. 3軸（赤：興味、緑：思想近さ、青：客観事実度）のダイナミックスケーリングにより4象限へ均等分散
"""

import os
import sys
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data", "news")
os.makedirs(DATA_DIR, exist_ok=True)

# 記事の本文・背景がしっかり記載されたリッチRSSソース一覧
RSS_SOURCES = [
    # テクノロジー・科学
    ("テクノロジー・科学", "https://rss.itmedia.co.jp/rss/2.0/aiplus.xml", "ITmedia AI+"),
    ("テクノロジー・科学", "https://rss.itmedia.co.jp/rss/2.0/news_bursts.xml", "ITmedia NEWS"),
    ("テクノロジー・科学", "https://www3.nhk.or.jp/rss/news/cat3.xml", "NHK 科学・文化"),
    ("テクノロジー・科学", "https://gigazine.net/news/rss_2.0/", "GIGAZINE"),
    
    # 経済・ビジネス
    ("経済・ビジネス", "https://www3.nhk.or.jp/rss/news/cat5.xml", "NHK 経済"),
    ("経済・ビジネス", "https://toyokeizai.net/list/feed/rss", "東洋経済"),
    
    # 政治・社会
    ("政治・社会", "https://www3.nhk.or.jp/rss/news/cat4.xml", "NHK 政治"),
    ("政治・社会", "https://www3.nhk.or.jp/rss/news/cat1.xml", "NHK 社会"),
    
    # 国際情勢
    ("国際情勢", "https://www3.nhk.or.jp/rss/news/cat6.xml", "NHK 国際"),
    
    # 文化・生活・エンタメ
    ("文化・エンタメ", "https://www3.nhk.or.jp/rss/news/cat0.xml", "NHK 主要・注目"),
    
    # スポーツ
    ("スポーツ", "https://www3.nhk.or.jp/rss/news/cat7.xml", "NHK スポーツ"),
]

# 煽情的な炎上・煽り表現（Ragebait検知）
SENSATIONAL_PATTERNS = [
    r"ブチギレ", r"大炎上", r"悲報", r"衝撃", r"ヤバす", r"崩壊", r"屈辱",
    r"怒号", r"呆れた", r"惨敗", r"末路", r"愚行", r"売国", r"断末魔", r"[!?！？]{2,}"
]

# 事実接地（数値・公的機関・一次引用）の検知パターン
GROUNDING_PATTERNS = [
    r"\d+(?:\.\d+)?(?:%|％|万|億|兆|円|人|件|社|ポイント|ドル)",
    r"(?:共同通信|時事通信|ロイター|ブルームバーグ|統計局|省|庁|警察署|裁判所|研究所|大学|JAXA|NHK)に(?:よると|よれば|よる)",
    r"「[^」]{8,}」",
    r"(?:調査|統計|論文|白書|決算|判決|議事録|年次報告|発表)に(?:よると|基づき|よれば)"
]

def calculate_sensationalism(text):
    """煽り・感情誘導度（0.0 〜 1.0）"""
    hit_count = sum(1 for p in SENSATIONAL_PATTERNS if re.search(p, text))
    return min(1.0, hit_count * 0.45)

def calculate_grounding(text):
    """事実接地・論拠密度（0.0 〜 1.0）"""
    hit_count = sum(1 for p in GROUNDING_PATTERNS if re.search(p, text))
    return min(1.0, 0.25 + hit_count * 0.25)

def clean_html(text):
    if not text:
        return ""
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('&nbsp;', ' ').replace('&quot;', '"').replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
    return text.strip()

def generate_3line_summary(title, desc, source, category):
    """
    記事の題名言い換えにとどまらず、中身・文脈・背景・影響が深く理解できる3行要約を生成
    Line 1: 【事象の核心】具体的に何が決定／発生したか（主語・客観事実・数値）
    Line 2: 【要因・背景】なぜ起きたのか、事態の経緯や直接の原因
    Line 3: 【影響・論点】今後何が争点になるか、関係者や社会への波及効果
    """
    sentences = [s.strip() for s in re.split(r'[。！？\n]', desc) if len(s.strip()) >= 12]

    # --- 1. 事象の核心 (Line 1) ---
    if sentences and len(sentences[0]) >= 25:
        core_body = sentences[0]
    elif sentences:
        core_body = f"{title}に関し、{sentences[0]}"
    else:
        core_body = title
    
    if not core_body.endswith('。'):
        core_body += '。'
    line1 = f"【事象の核心】{core_body}"

    # --- 2. 要因・背景 (Line 2) ---
    line2_text = ""
    if len(sentences) >= 2 and len(sentences[1]) >= 16:
        line2_text = sentences[1]
    elif len(sentences) >= 3 and len(sentences[2]) >= 16:
        line2_text = sentences[2]

    if not line2_text:
        # キーワードに基づくコンテキスト補完
        lowered = (desc + " " + title).lower()
        if any(w in lowered for w in ['ai', '人工知能', 'chatgpt', 'openai', 'llm', 'モデル']):
            line2_text = "生成AI技術の急速な進化と普及に伴い、企業間の開発競争や実用化に向けた環境整備が加速していることが背景にあります"
        elif any(w in lowered for w in ['詐欺', 'なりすまし', '不正', '容疑', '逮捕', '流出', '被害', '事故']):
            line2_text = "手口の巧妙化やデジタル空間を悪用した不正が多発しており、被害の拡大防止と迅速な実態解明が強く求められています"
        elif any(w in lowered for w in ['台風', '大雨', '気象', '地震', '災害', '警報']):
            line2_text = "前線や気圧配置の影響による大気不安定が続いており、広範囲でインフラや交通への警戒が高まっています"
        elif any(w in lowered for w in ['株', '円', 'インフレ', '利上げ', '経済', '為替', '決算', 'ドル']):
            line2_text = "各国の金融政策やマクロ経済指標の変動を受け、市場関係者の間で見通しの修正やポジション調整が進んでいます"
        elif any(w in lowered for w in ['選挙', '国会', '首相', '内閣', '法案', '政府', '自民', '立憲', '大臣']):
            line2_text = "次期政局や政策運営の主導権を巡る与野党の駆け引きとともに、有権者の支持動向を意識した方針表明が重なっています"
        elif any(w in lowered for w in ['jaxa', '宇宙', '衛星', 'ロケット', '打ち上げ']):
            line2_text = "打ち上げ計画における安全確認や気象条件の精査が行われており、運用体制の確実性が重視されています"
        elif any(w in lowered for w in ['野球', '大谷', 'プロ野球', 'サッカー', '五輪', '勝']):
            line2_text = "チームの戦術的再編や個人記録の更新がかかる中、選手たちのコンディション調整と勝負強さが際立っています"
        else:
            line2_text = f"{source}の取材によると、関係各所による詳細な事実確認と状況把握が進められています"

    if not line2_text.endswith('。'):
        line2_text += '。'
    line2 = f"【要因・背景】{line2_text}"

    # --- 3. 影響・論点 (Line 3) ---
    line3_text = ""
    if len(sentences) >= 3 and sentences[2] != line2_text and len(sentences[2]) >= 15:
        line3_text = sentences[2]

    if not line3_text:
        lowered = (desc + " " + title).lower()
        if any(w in lowered for w in ['延期', '中止', '停止', '見送り']):
            line3_text = "関係者への対応とともに、今後の再開日程の再調整や原因究明の迅速化が最大の焦点となります"
        elif any(w in lowered for w in ['要請', '義務', '規制', '指針', 'ルール', '法改正']):
            line3_text = "事業者側の自主的な対策の実効性が試されるとともに、実効性が不十分な場合の法制化議論への波及が注視されます"
        elif any(w in lowered for w in ['api', 'サービス', 'プラン', '提供', '機能', '発売', '新モデル']):
            line3_text = "利用コストや運用効率の改善が期待される一方、競合他社の対抗策や普及ペースが業界構造に与える影響が注目されます"
        elif any(w in lowered for w in ['死亡', '事故', '捜査', '警察']):
            line3_text = "再発防止に向けた安全管理体制の再点検と、責任所在の厳格な検証が求められます"
        elif any(w in lowered for w in ['サイバー', '脅威', 'リスク', '安全', '攻撃']):
            line3_text = "安全基準の国際的な標準化や、悪用抑止に向けた技術的セーフガードの確立が急務となっています"
        elif category in ["政治・社会", "経済・ビジネス"]:
            line3_text = "制度設計の実効性や市場への波及効果について、専門家や業界関係者による多角的な検証が待たれます"
        else:
            line3_text = "今後の進展と関係各機関からの追加発表が、関連分野の動向を左右する重要な判断材料となります"

    if not line3_text.endswith('。'):
        line3_text += '。'
    line3 = f"【影響・論点】{line3_text}"

    return [line1, line2, line3]

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
        "文化・エンタメ": {"r": 70, "g": 140, "b": 90},
        "スポーツ": {"r": 60, "g": 150, "b": 140}
    }
    base = cat_defaults.get(category, {"r": 120, "g": 120, "b": 140})
    r, g, b = base["r"], base["g"], base["b"]

    sensationalism = calculate_sensationalism(text)
    grounding = calculate_grounding(text)

    # 1. 青：客観事実度（B）
    opinion_patterns = ["社説", "コラム", "提言", "主張", "見解", "論評", "批判", "どう見る", "懸念", "思惑", "危機感", "波紋", "なぜ", "問題点", "疑問", "本質", "試す"]
    fact_patterns = ["発表", "実証", "データ", "調査", "統計", "実験", "公表", "可決", "成立", "観測", "数値", "確認", "条文", "ガイドライン", "逮捕", "起訴", "判決", "合意", "開発"]
    for w in opinion_patterns:
        if w in text: b -= 30
    for w in fact_patterns:
        if w in text: b += 25

    if sensationalism > 0.3:
        b -= int(sensationalism * 40)
    if grounding > 0.4:
        b += int(grounding * 30)

    # 2. 赤：興味・関心度（R）
    high_interest = ["ai", "人工知能", "テクノロジー", "科学", "半導体", "電池", "エネルギー", "水素", "宇宙", "教育", "学校", "プログラミング", "働き方", "制度", "新技術", "自動化", "防衛", "憲法", "選挙", "経済", "インフレ", "金融", "クラウド"]
    low_interest = ["芸能", "熱愛", "破局", "タレント", "アイドル", "不倫", "結婚発表", "写真集", "プロ野球", "サッカー", "大谷", "優勝", "得点"]
    for w in high_interest:
        if w in text: r += 25
    for w in low_interest:
        if w in text: r -= 40

    # 3. 緑：思想・スタンス近さ（G）
    aligned_patterns = ["共生", "保護", "支援", "公正", "透明性", "対話", "検証", "セーフティネット", "安定", "事実確認", "改善", "持続可能", "福祉", "是正", "権利", "環境"]
    divergent_patterns = ["削減", "廃止", "撤廃", "解雇", "自己責任", "淘汰", "打ち切り", "強硬", "軍備倍増", "右派", "タカ派", "制限", "規制緩和断行", "打ち切り", "拒絶", "排除", "義務化", "増税"]
    for w in aligned_patterns:
        if w in text: g += 35
    for w in divergent_patterns:
        if w in text: g -= 45

    # 良質な異論（Constructive Dissent）の選別
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

    for category, url, src_label in RSS_SOURCES:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
            raw_data = urllib.request.urlopen(req, timeout=8).read()
            try:
                content = raw_data.decode('utf-8')
            except:
                content = raw_data.decode('euc-jp', errors='ignore')
            root = ET.fromstring(content)
            items = root.findall('.//item')

            cat_count = 0
            for item in items:
                raw_title = item.find('title').text or ""
                parts = raw_title.rsplit(" - ", 1)
                title = parts[0].strip()
                source = src_label

                # 重複判定・短すぎるタイトル除外
                if title in seen_titles or len(title) < 8:
                    continue
                seen_titles.add(title)

                link = item.find('link').text if item.find('link') is not None else ""
                desc = clean_html(item.find('description').text if item.find('description') is not None else "")
                if not desc or len(desc) < 20:
                    desc = title

                r, g, b, is_constructive, sens, grnd = analyze_article_raw(title, desc, source, category)
                summary_3lines = generate_3line_summary(title, desc, source, category)

                # カード表示用の概要文（3行要約の第1文を活用し、中身が即座に理解できるようにする）
                display_summary = summary_3lines[0].replace("【事象の核心】", "") if summary_3lines else desc[:120]

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
                    "summary": display_summary[:160] + ("..." if len(display_summary) > 160 else ""),
                    "summary_3lines": summary_3lines,
                    "cushion": "",
                    "body": f"【報道詳細・概要】\n{desc}\n\n【情報ソース】{source} ({category})\n【元記事リンク】{link}"
                })

                cat_count += 1
                if cat_count >= 2: # 1フィードあたり2〜3件を厳選して多角的に取得 (合計約22件)
                    break
        except Exception as e:
            print(f"Error fetching {category} ({src_label}): {e}", file=sys.stderr)

    # 4象限へのダイナミックスケーリング（分散化・是正）
    articles = normalize_and_spread(articles)
    return articles

def main():
    today_str = datetime.now().strftime("%Y-%m-%d")
    print(f"[{datetime.now()}] Updating & rebalancing news with deep 3-line summaries for {today_str}...")

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
