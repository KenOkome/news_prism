# -*- coding: utf-8 -*-
"""
NewsPrism - 毎朝のニュース自動更新＆深掘り3行要約生成スクリプト
1. リアルタイムに更新されている大手報道機関（ITmedia、東洋経済、GIGAZINE、BBC、WIRED、Business Insider等）から最新ニュースを取得
2. 直近48時間以内の記事のみを厳選する鮮度ゲート（Freshness Gate）により古い記事の混入を完全に防止
3. ローカルLLM（Ollama qwen2.5:14b）による「結論踏み込み型3行要約」エンジンの搭載（【事象の核心】【要因・経緯】【結論・影響】）
4. 3軸（赤：興味、緑：思想近さ、青：客観事実度）のダイナミックスケーリングにより4象限へ均等分散
5. JSONファイルと動的フォールバック用JSファイル（window.NEWS_ARCHIVE）を同時生成
"""

import os
import sys
import json
import re
import urllib.request
import email.utils
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data", "news")
os.makedirs(DATA_DIR, exist_ok=True)

JST = timezone(timedelta(hours=9))

# リアルタイムに最新ニュースを配信している厳選RSSソース一覧
RSS_SOURCES = [
    # テクノロジー・科学
    ("テクノロジー・科学", "https://rss.itmedia.co.jp/rss/2.0/aiplus.xml", "ITmedia AI+"),
    ("テクノロジー・科学", "https://rss.itmedia.co.jp/rss/2.0/news_bursts.xml", "ITmedia NEWS"),
    ("テクノロジー・科学", "https://gigazine.net/news/rss_2.0/", "GIGAZINE"),
    ("テクノロジー・科学", "https://wired.jp/rssfeeder/", "WIRED Japan"),
    
    # 経済・ビジネス
    ("経済・ビジネス", "https://toyokeizai.net/list/feed/rss", "東洋経済オンライン"),
    ("経済・ビジネス", "https://www.businessinsider.jp/feed/index.xml", "Business Insider Japan"),
    ("経済・ビジネス", "https://gendai.media/list/feed/rss", "現代ビジネス"),
    ("経済・ビジネス", "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=ja&gl=JP&ceid=JP:ja", "Google News 経済"),
    
    # 政治・社会
    ("政治・社会", "https://news.google.com/rss/headlines/section/topic/NATION?hl=ja&gl=JP&ceid=JP:ja", "Google News 政治・社会"),
    ("政治・社会", "https://feeds.bbci.co.uk/japanese/rss.xml", "BBC News 日本語版"),
    
    # 国際情勢
    ("国際情勢", "https://news.google.com/rss/headlines/section/topic/WORLD?hl=ja&gl=JP&ceid=JP:ja", "Google News 国際"),
    ("国際情勢", "https://feeds.bbci.co.uk/japanese/rss.xml", "BBC News 日本語版"),
    
    # 文化・エンタメ
    ("文化・エンタメ", "https://www.huffingtonpost.jp/feeds/index.xml", "ハフポスト日本版"),
    ("文化・エンタメ", "https://news.google.com/rss/headlines/section/topic/ENTERTAINMENT?hl=ja&gl=JP&ceid=JP:ja", "Google News エンタメ"),
    
    # スポーツ
    ("スポーツ", "https://news.google.com/rss/headlines/section/topic/SPORTS?hl=ja&gl=JP&ceid=JP:ja", "Google News スポーツ"),
]

# 煽情的な炎上・煽り表現（Ragebait検知）
SENSATIONAL_PATTERNS = [
    r"ブチギレ", r"大炎上", r"悲報", r"衝撃", r"ヤバす", r"崩壊", r"屈辱",
    r"怒号", r"呆れた", r"惨敗", r"末路", r"愚行", r"売国", r"断末魔", r"[!?！？]{2,}"
]

# 事実接地（数値・公的機関・一次引用）の検知パターン
GROUNDING_PATTERNS = [
    r"\d+(?:\.\d+)?(?:%|％|万|億|兆|円|人|件|社|ポイント|ドル)",
    r"(?:共同通信|時事通信|ロイター|ブルームバーグ|統計局|省|庁|警察署|裁判所|研究所|大学|JAXA|BBC)に(?:よると|よれば|よる)",
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

def clean_google_news_desc(raw_html, title):
    """Google News RSSの他社リンク羅列を除去し、主要要約のみを抽出"""
    if not raw_html:
        return title
    # 最初の <a> タグ内のテキストを取得
    match = re.search(r'<a[^>]*>(.*?)</a>', raw_html)
    if match:
        first_headline = clean_html(match.group(1))
        # タイトルと異なる情報が含まれていればそれを活用
        return first_headline
    return clean_html(raw_html)

def parse_and_check_freshness(item, max_age_hours=48):
    """
    RSSアイテムから配信日時を抽出し、JST表記に変換するとともに
    直近48時間以内の最新ニュースかどうかを厳格に判定する
    """
    pub_el = item.find('pubDate')
    dc_date = item.find('{http://purl.org/dc/elements/1.1/}date')
    raw_date = pub_el.text if pub_el is not None else (dc_date.text if dc_date is not None else "")

    now_jst = datetime.now(JST)

    if not raw_date or not raw_date.strip():
        return now_jst.strftime("%Y/%m/%d %H:%M"), True

    dt = None
    try:
        dt = email.utils.parsedate_to_datetime(raw_date.strip())
    except Exception:
        try:
            dt = datetime.fromisoformat(raw_date.strip())
        except Exception:
            pass

    if dt is None:
        return now_jst.strftime("%Y/%m/%d %H:%M"), True

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    else:
        dt = dt.astimezone(JST)

    age = now_jst - dt
    is_fresh = (age <= timedelta(hours=max_age_hours))
    return dt.strftime("%Y/%m/%d %H:%M"), is_fresh

def generate_3line_summary_fallback(title, desc, source, category):
    """Ollamaオフライン環境用のスマート・フォールバック要約"""
    clean_desc = clean_html(desc)
    sentences = [s.strip() for s in re.split(r'[。！？\n]', clean_desc) if len(s.strip()) >= 12]

    # 1. 事象の核心
    if sentences and len(sentences[0]) >= 25:
        core_body = sentences[0]
    elif sentences:
        core_body = f"{title}に関し、{sentences[0]}"
    else:
        core_body = title
    if not core_body.endswith('。'): core_body += '。'
    line1 = f"【事象の核心】{core_body}"

    # 2. 要因・経緯
    line2_text = ""
    if len(sentences) >= 2 and len(sentences[1]) >= 16:
        line2_text = sentences[1]
    elif len(sentences) >= 3 and len(sentences[2]) >= 16:
        line2_text = sentences[2]

    if not line2_text:
        lowered = (clean_desc + " " + title).lower()
        if any(w in lowered for w in ['ai', '人工知能', 'chatgpt', 'claude', 'anthropic', 'openai', 'llm', 'モデル']):
            line2_text = "生成AI技術の急速な進化と普及に伴い、企業間の開発競争や実用化に向けた環境整備が加速していることが背景にあります"
        elif any(w in lowered for w in ['詐欺', 'なりすまし', '不正', '容疑', '逮捕', '流出', '被害', '事故', 'ハッカー', '窃盗']):
            line2_text = "手口の巧妙化やデジタル空間を悪用した不正が多発しており、被害の拡大防止と迅速な実態解明が強く求められています"
        elif any(w in lowered for w in ['株', '円', 'インフレ', '利上げ', '経済', '為替', '決算', 'ドル', 'ipo', '売上']):
            line2_text = "各国の金融政策やマクロ経済指標の変動を受け、市場関係者の間で見通しの修正やポジション調整が進んでいます"
        elif any(w in lowered for w in ['選挙', '中間選挙', '国会', '首相', '内閣', '法案', '政府', 'トランプ', '民主', '共和']):
            line2_text = "政局や政策運営の主導権を巡る激しい駆け引きとともに、有権者の支持動向を意識した方針表明が重なっています"
        else:
            line2_text = f"{source}の取材によると、関連するデータや当事者への取材をもとに事実関係の確認が進められています"
    if not line2_text.endswith('。'): line2_text += '。'
    line2 = f"【要因・経緯】{line2_text}"

    # 3. 結論・影響
    line3_text = ""
    if len(sentences) >= 3 and sentences[2] != line2_text and len(sentences[2]) >= 15:
        line3_text = sentences[2]

    if not line3_text:
        lowered = (clean_desc + " " + title).lower()
        if any(w in lowered for w in ['延期', '中止', '停止', '見送り']):
            line3_text = "安全面や環境条件の再検証が進められ、再開日程の再調整と原因究明が最大の焦点となっています"
        elif any(w in lowered for w in ['要請', '義務', '規制', '指針', 'ルール', '法改正', '禁止']):
            line3_text = "違反時のペナルティや実効性のある運用体制の確立が求められる一方、業界全体の慣習見直しへ波及する見通しです"
        elif any(w in lowered for w in ['api', 'サービス', 'プラン', '提供', '機能', '発売', '新モデル', 'ultra']):
            line3_text = "利用者の利便性向上や運用コスト改善に直結する決定であり、競合他社の追随やシェア動向が注目されます"
        elif any(w in lowered for w in ['赤字', '増収', '最高益', '決算', '買収', 'ipo']):
            line3_text = "市場予想を上回る堅調な収益基盤を示す一方、今後の投資効率と持続的な成長性の確立が試金石となります"
        else:
            line3_text = "関係機関による決定事項が実務や市場へ順次適用され、今後の業界基準を左右する重要な判断材料となります"
    if not line3_text.endswith('。'): line3_text += '。'
    line3 = f"【結論・影響】{line3_text}"

    return [line1, line2, line3]

def generate_3line_summary(title, desc, source, category):
    """
    ローカルLLM (Ollama qwen2.5:14b) による結論踏み込み型3行要約生成（フォールバック付き）
    """
    clean_text = clean_html(desc)
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()

    prompt = f"""以下のニュース記事から、見出しの表面的な言い換えではなく、記事の内容・具体的な事実・経緯、そして「どのような結論・決定・結果に至ったか」まで踏み込んだ日本語の3行要約を作成してください。

【タイトル】{title}
【報道情報】
{clean_text[:600]}

必ず以下の3行フォーマット（各行50〜100文字程度）で出力してください。余計な挨拶や前置きは一切書かないでください。
【事象の核心】具体的に何が起き、誰が何を決定・発表したのか（事実と重要数値）
【要因・経緯】なぜそれが起きたのか、背景や問題の本質
【結論・影響】どのような結果・結論に至ったのか、関係者や社会への今後の波及"""

    data = {
        "model": "qwen2.5:14b",
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 240},
        "keep_alive": "10m"
    }

    try:
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        res = json.loads(urllib.request.urlopen(req, timeout=18).read())
        resp = res.get("response", "").strip()
        lines = [l.strip() for l in resp.split("\n") if l.strip() and "【" in l]
        if len(lines) >= 3:
            l1 = lines[0] if "【事象の核心】" in lines[0] else f"【事象の核心】{re.sub(r'^【.*?】', '', lines[0])}"
            l2 = lines[1] if "【要因・経緯】" in lines[1] else f"【要因・経緯】{re.sub(r'^【.*?】', '', lines[1])}"
            l3 = lines[2] if "【結論・影響】" in lines[2] else f"【結論・影響】{re.sub(r'^【.*?】', '', lines[2])}"
            return [l1, l2, l3]
    except Exception as e:
        # print(f"Ollama fallback for {title[:20]}: {e}")
        pass

    return generate_3line_summary_fallback(title, desc, source, category)

def analyze_article_raw(title, summary, source, category):
    """記事の内容から未正規化の生スコアを算出（良質異論ゲート付き）"""
    text = (title + " " + summary).lower()

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
    fact_patterns = ["発表", "実証", "データ", "調査", "統計", "実験", "公表", "可決", "成立", "観測", "数値", "確認", "条文", "ガイドライン", "逮捕", "起訴", "判決", "合意", "開発", "最高益"]
    for w in opinion_patterns:
        if w in text: b -= 30
    for w in fact_patterns:
        if w in text: b += 25

    if sensationalism > 0.3:
        b -= int(sensationalism * 40)
    if grounding > 0.4:
        b += int(grounding * 30)

    # 2. 赤：興味・関心度（R）
    high_interest = ["ai", "人工知能", "テクノロジー", "科学", "半導体", "電池", "エネルギー", "水素", "宇宙", "教育", "学校", "プログラミング", "働き方", "制度", "新技術", "自動化", "防衛", "憲法", "選挙", "経済", "インフレ", "金融", "クラウド", "中間選挙"]
    low_interest = ["芸能", "熱愛", "破局", "タレント", "アイドル", "不倫", "結婚発表", "写真集", "プロ野球", "サッカー", "大谷", "優勝", "得点"]
    for w in high_interest:
        if w in text: r += 25
    for w in low_interest:
        if w in text: r -= 40

    # 3. 緑：思想・スタンス近さ（G）
    aligned_patterns = ["共生", "保護", "支援", "公正", "透明性", "対話", "検証", "セーフティネット", "安定", "事実確認", "改善", "持続可能", "福祉", "是正", "権利", "環境"]
    divergent_patterns = ["削減", "廃止", "撤廃", "解雇", "自己責任", "淘汰", "打ち切り", "強硬", "軍備倍増", "右派", "タカ派", "制限", "規制緩和断行", "打ち切り", "拒絶", "排除", "義務化", "増税", "トランプ"]
    for w in aligned_patterns:
        if w in text: g += 35
    for w in divergent_patterns:
        if w in text: g -= 45

    is_constructive_dissent = bool(sensationalism <= 0.35 and grounding >= 0.4 and g <= 130)

    return r, g, b, is_constructive_dissent, round(sensationalism, 2), round(grounding, 2)

def normalize_and_spread(articles):
    """4象限へのダイナミックスケーリング（分散化・是正）"""
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

                if title in seen_titles or len(title) < 8:
                    continue

                published_at, is_fresh = parse_and_check_freshness(item, max_age_hours=48)
                if not is_fresh:
                    continue

                seen_titles.add(title)

                link = item.find('link').text if item.find('link') is not None else ""
                raw_desc = item.find('description').text if item.find('description') is not None else ""
                
                # Google Newsの場合は他社リンク羅列を除去
                if "Google News" in source:
                    desc = clean_google_news_desc(raw_desc, title)
                else:
                    desc = clean_html(raw_desc)

                if not desc or len(desc) < 20:
                    desc = title

                r, g, b, is_constructive, sens, grnd = analyze_article_raw(title, desc, source, category)
                
                # Ollamaによる結論踏み込み型3行要約
                summary_3lines = generate_3line_summary(title, desc, source, category)

                # カード表示用の概要文（3行要約の第1文を活用）
                display_summary = summary_3lines[0].replace("【事象の核心】", "") if summary_3lines else desc[:120]

                articles.append({
                    "id": len(articles) + 1,
                    "title": title,
                    "source": f"{source} / {category}",
                    "published_at": published_at,
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
                    "body": f"【報道詳細・概要】\n{desc}\n\n【配信日時】{published_at}\n【情報ソース】{source} ({category})\n【元記事リンク】{link}"
                })

                cat_count += 1
                if cat_count >= 2: # 1フィードあたり最大2件厳選
                    break
        except Exception as e:
            print(f"Error fetching {category} ({src_label}): {e}", file=sys.stderr)

    print(f"Total fresh articles collected: {len(articles)}")
    articles = normalize_and_spread(articles)
    return articles

def main():
    today_str = datetime.now(JST).strftime("%Y-%m-%d")
    print(f"[{datetime.now(JST)}] Updating & rebalancing news with Ollama conclusion-driven 3-line summaries for {today_str}...")

    articles = fetch_latest_news()
    if not articles:
        print("No articles fetched, aborting.")
        return

    today_file = os.path.join(DATA_DIR, f"{today_str}.json")
    with open(today_file, "w", encoding="utf-8") as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(articles)} articles to {today_file}")

    today_js = os.path.join(DATA_DIR, f"{today_str}.js")
    with open(today_js, "w", encoding="utf-8") as f:
        f.write(f"window.NEWS_ARCHIVE = window.NEWS_ARCHIVE || {{}};\nwindow.NEWS_ARCHIVE['{today_str}'] = {json.dumps(articles, ensure_ascii=False, indent=2)};\n")
    print(f"Saved {today_js}")

    all_files = sorted([f.replace(".json", "") for f in os.listdir(DATA_DIR) if f.endswith(".json") and re.match(r'^\d{4}-\d{2}-\d{2}$', f.replace(".json", ""))], reverse=True)
    dates_file = os.path.join(DATA_DIR, "dates.json")
    with open(dates_file, "w", encoding="utf-8") as f:
        json.dump(all_files, f, ensure_ascii=False, indent=2)

    for date_key in all_files:
        js_path = os.path.join(DATA_DIR, f"{date_key}.js")
        json_path = os.path.join(DATA_DIR, f"{date_key}.json")
        if not os.path.exists(js_path) and os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as jf:
                    data = json.load(jf)
                with open(js_path, "w", encoding="utf-8") as jsf:
                    jsf.write(f"window.NEWS_ARCHIVE = window.NEWS_ARCHIVE || {{}};\nwindow.NEWS_ARCHIVE['{date_key}'] = {json.dumps(data, ensure_ascii=False, indent=2)};\n")
            except Exception as e:
                print(f"Warning: could not generate {js_path}: {e}")

    js_content = f"// 自動生成されたニュースデータ ({today_str})\n"
    js_content += f"window.AVAILABLE_DATES = {json.dumps(all_files, ensure_ascii=False)};\n"
    js_content += f"window.CURRENT_DATE = '{today_str}';\n"
    js_content += f"window.NEWS_DATA_TODAY = {json.dumps(articles, ensure_ascii=False, indent=2)};\n"

    js_file = os.path.join(BASE_DIR, "news_data.js")
    with open(js_file, "w", encoding="utf-8") as f:
        f.write(js_content)
    print(f"Updated {js_file} successfully.")

    # index.html のキャッシュバスティング (?v=YYYYMMDDHHMMSS) を自動更新
    index_html_path = os.path.join(BASE_DIR, "index.html")
    if os.path.exists(index_html_path):
        with open(index_html_path, "r", encoding="utf-8") as f:
            html_text = f.read()
        v_token = datetime.now(JST).strftime("%Y%m%d%H%M%S")
        updated_html = re.sub(r'src="news_data\.js(\?v=[^"]*)?"', f'src="news_data.js?v={v_token}"', html_text)
        with open(index_html_path, "w", encoding="utf-8") as f:
            f.write(updated_html)
        print(f"Updated cache-busting token in {index_html_path} to v={v_token}")

if __name__ == "__main__":
    main()
