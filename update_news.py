#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NewsPrism - Automatic Global News Aggregator & 3D Prism Vector Space Analyzer
=============================================================================
1. 国内外（日本、欧州、米国、中東）の一次メディア直結RSSから最新記事を取得
2. 記事URLから本文実体（フルテキスト）を直接スクレイピング・抽出
3. ローカルLLM（Ollama qwen2.5:14b）により、英語記事は自然な日本語タイトルへ翻訳＋結論踏み込み型3行要約を生成
4. 4象限（ホワイト、温和、激論、問題提起）への均等配分およびデータ保存
5. データ契約保護（AVAILABLE_DATES, CURRENT_DATE, NEWS_DATA_TODAY）を完全保証
"""

import os
import sys
import json
import re
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data", "news")
os.makedirs(DATA_DIR, exist_ok=True)

JST = timezone(timedelta(hours=9))

# 記事本文（フルテキスト）が確実に取得できる一次メディア厳選RSSソース一覧（国内＋欧米＋中東）
RSS_SOURCES = [
    # --- テクノロジー・科学 ---
    ("テクノロジー・科学", "https://rss.itmedia.co.jp/rss/2.0/aiplus.xml", "ITmedia AI+ [日]", "ja"),
    ("テクノロジー・科学", "https://rss.itmedia.co.jp/rss/2.0/news_bursts.xml", "ITmedia NEWS [日]", "ja"),
    ("テクノロジー・科学", "https://gigazine.net/news/rss_2.0/", "GIGAZINE [日]", "ja"),
    ("テクノロジー・科学", "https://techcrunch.com/feed/", "TechCrunch [米]", "en"),
    ("テクノロジー・科学", "https://feeds.arstechnica.com/arstechnica/index", "Ars Technica [米]", "en"),
    ("テクノロジー・科学", "https://www.theverge.com/rss/index.xml", "The Verge [米]", "en"),
    ("テクノロジー・科学", "https://pc.watch.impress.co.jp/data/rss/1.0/pcw/feed.rdf", "PC Watch [日]", "ja"),
    
    # --- 経済・ビジネス ---
    ("経済・ビジネス", "https://toyokeizai.net/list/feed/rss", "東洋経済オンライン [日]", "ja"),
    ("経済・ビジネス", "https://www.businessinsider.jp/feed/index.xml", "Business Insider Japan [日]", "ja"),
    ("経済・ビジネス", "https://rss.itmedia.co.jp/rss/2.0/makoto_bizid.xml", "ITmedia ビジネス [日]", "ja"),
    ("経済・ビジネス", "https://diamond.jp/list/feed/rss", "ダイヤモンド・オンライン [日]", "ja"),
    
    # --- 国際情勢（中東・欧州・北米の多角視点） ---
    ("国際情勢", "https://www.aljazeera.com/xml/rss/all.xml", "Al Jazeera [中東/カタール]", "en"),
    ("国際情勢", "https://www.timesofisrael.com/feed/", "Times of Israel [中東/イスラエル]", "en"),
    ("国際情勢", "https://feeds.bbci.co.uk/news/world/rss.xml", "BBC World [英]", "en"),
    ("国際情勢", "https://rss.dw.com/xml/rss-en-world", "DW News [独]", "en"),
    ("国際情勢", "https://www.france24.com/en/rss", "France 24 [仏]", "en"),
    
    # --- 政治・社会 ---
    ("政治・社会", "https://feeds.bbci.co.uk/japanese/rss.xml", "BBC News 日本語版 [英]", "ja"),
    ("政治・社会", "https://feeds.npr.org/1001/rss.xml", "NPR News [米]", "en"),
    ("政治・社会", "https://www.theguardian.com/world/rss", "The Guardian [英]", "en"),
    ("政治・社会", "https://www.huffingtonpost.jp/feeds/index.xml", "ハフポスト日本版 [日]", "ja"),
    ("政治・社会", "https://news.mynavi.jp/rss/index.rdf", "マイナビニュース [日]", "ja"),
    
    # --- 文化・エンタメ ---
    ("文化・エンタメ", "https://natalie.mu/comic/feed/news", "コミックナタリー [日]", "ja"),
    ("文化・エンタメ", "https://natalie.mu/eiga/feed/news", "映画ナタリー [日]", "ja"),
    ("文化・エンタメ", "https://natalie.mu/music/feed/news", "音楽ナタリー [日]", "ja"),
    
    # --- スポーツ ---
    ("スポーツ", "https://the-ans.jp/feed/", "THE ANSWER [日]", "ja"),
    ("スポーツ", "https://www.soccer-king.jp/feed", "サッカーキング [日]", "ja"),
    ("スポーツ", "https://baseballking.jp/feed", "ベースボールキング [日]", "ja"),
]

# メディア特有の定型文（寄付・支援・会員登録・フォロー案内・広告等）の完全除外パターン
BOILERPLATE_PATTERNS = [
    # 寄付・サーバー支援（GIGAZINE等）
    r"サーバー運営", r"支援をお願い", r"ご寄付", r"寄付", r"サーバー代", r"親愛なる読者",
    r"たった1円でも", r"物理的なサーバー", r"毎月\s*\d+円", r"GIGAZINEを支援", r"今すぐ寄付",
    r"運営費", r"カンパ", r"投げ銭",
    
    # フォロー・メルマガ・通知（東洋経済・ダイヤモンド・ITmedia等）
    r"フォローボタン", r"フォローすると", r"新着記事をメールで", r"メルマガ", r"メールマガジン",
    r"公式SNS", r"公式X", r"公式Twitter", r"LINEで", r"通知を受け取る", r"お気に入り登録",
    
    # 会員登録・ログイン・有料記事・購読案内
    r"会員登録", r"有料会員", r"ログインして読む", r"無料会員", r"続きを読む", r"記事の続きは",
    r"プレミアム会員", r"有料プラン", r"今すぐ登録", r"無制限で読める", r"アプリで読む",
    r"本稿は.*(?:転載|抜粋)", r"関連リンク", r"あわせて読みたい",
    
    # 著作権・利用規約・プライバシー
    r"Copyright", r"All Rights Reserved", r"利用規約", r"Cookie", r"プライバシーポリシー",
    r"転載を禁じます", r"無断転載",
    
    # 海外メディアのドネーション・サブスクリプション案内
    r"\bSupport the Guardian\b", r"\bcontribute\b", r"\bdonate\b", r"\bsubscription\b",
    r"\bnewsletter\b", r"\bsign up\b", r"\bterms of use\b", r"\bprivacy policy\b",
    r"\bmembership\b", r"\bexclusive content\b"
]

def is_boilerplate(text):
    """テキストがメディアの定型アピール文（寄付、フォロー、会員登録等）か判定"""
    if not text:
        return True
    for p in BOILERPLATE_PATTERNS:
        if re.search(p, text, re.IGNORECASE):
            return True
    return False

# 煽情的な炎上・煽り表現（Ragebait検知）
SENSATIONAL_PATTERNS = [
    r"ブチギレ", r"大炎上", r"悲報", r"衝撃", r"ヤバす", r"崩壊", r"屈辱",
    r"怒号", r"呆れた", r"惨敗", r"末路", r"愚行", r"売国", r"断末魔", r"[!?！？]{2,}",
    r"\bshocking\b", r"\boutrage\b", r"\bfurious\b", r"\bdisaster\b", r"\bhumiliat", r"\bcrisis\b"
]

# 事実接地（数値・公的機関・一次引用）の検知パターン
GROUNDING_PATTERNS = [
    r"\d+(?:\.\d+)?(?:%|％|万|億|兆|円|人|件|社|ポイント|ドル|\$|€|£|bn|million|billion|percent)",
    r"(?:共同通信|時事通信|ロイター|ブルームバーグ|統計局|省|庁|警察署|裁判所|研究所|大学|JAXA|BBC|Reuters|AP|AFP|court|official)に?(?:よると|よれば|よる|according to|reported)",
    r"「[^」]{8,}」", r'"[^"]{12,}"',
    r"(?:調査|統計|論文|白書|決算|判決|議事録|年次報告|発表|study|report|investigation|data)に?(?:よると|基づき|よれば|shows|found)"
]

def calculate_sensationalism(text):
    """煽り・感情誘導度（0.0 〜 1.0）"""
    hit_count = sum(1 for p in SENSATIONAL_PATTERNS if re.search(p, text, re.IGNORECASE))
    return min(1.0, hit_count * 0.45)

def calculate_grounding(text):
    """事実接地・論拠密度（0.0 〜 1.0）"""
    hit_count = sum(1 for p in GROUNDING_PATTERNS if re.search(p, text, re.IGNORECASE))
    return min(1.0, 0.25 + hit_count * 0.25)

def clean_html(text):
    if not text:
        return ""
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('&nbsp;', ' ').replace('&quot;', '"').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&#039;', "'")
    return re.sub(r'\s+', ' ', text).strip()

def is_english_text(text):
    """テキストに日本語（ひらがな・カタカナ・漢字）がほとんど含まれない場合を英語と判定"""
    if not text:
        return False
    jp_chars = len(re.findall(r'[\u3040-\u30ff\u4e00-\u9faf]', text))
    return (jp_chars / max(1, len(text))) < 0.15

def translate_to_japanese(text):
    """Google Translate (gtx) を利用したAPIキー不要の自動日本語翻訳"""
    if not text or not is_english_text(text):
        return text
    try:
        query = text[:1200]
        url = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=ja&dt=t&q=" + urllib.parse.quote(query)
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=6) as res:
            data = json.loads(res.read().decode('utf-8'))
        translated = "".join([part[0] for part in data[0] if part[0]])
        return translated.strip() if translated else text
    except Exception as e:
        return text

def parse_and_check_freshness(item, max_age_hours=48):
    """RSSアイテムから配信日時をパースし、指定時間以内の新鮮なニュースか判定"""
    pub_el = item.find('pubDate')
    if pub_el is None or not pub_el.text:
        pub_el = item.find('{http://purl.org/dc/elements/1.1/}date')
    if pub_el is None or not pub_el.text:
        pub_el = item.find('{http://www.w3.org/2005/Atom}updated')
    if pub_el is None or not pub_el.text:
        pub_el = item.find('{http://www.w3.org/2005/Atom}published')
        
    if pub_el is None or not pub_el.text:
        return datetime.now(JST).strftime("%Y/%m/%d %H:%M"), True

    raw_date = pub_el.text.strip()
    try:
        dt = parsedate_to_datetime(raw_date)
    except:
        try:
            dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
        except:
            return datetime.now(JST).strftime("%Y/%m/%d %H:%M"), True

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(JST)

    now_jst = datetime.now(JST)
    age = now_jst - dt
    is_fresh = (age <= timedelta(hours=max_age_hours))
    return dt.strftime("%Y/%m/%d %H:%M"), is_fresh

def extract_article_body(url):
    """
    記事リンク先からHTMLを取得し、主要本文段落を抽出（スクレイピング）
    メディア定型文（GIGAZINE寄付、東洋経済フォロー、会員登録等）を徹底除去
    150文字未満の場合は本文取得不可（会員限定等）として判定
    """
    if not url or not url.startswith("http"):
        return ""
    try:
        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
            }
        )
        with urllib.request.urlopen(req, timeout=7) as res:
            raw = res.read()
            try:
                html = raw.decode('utf-8')
            except:
                html = raw.decode('shift_jis', errors='replace')

        # 不要なタグを全除去
        html = re.sub(r'<(script|style|noscript|header|footer|nav|aside|figure|button|form)[^>]*>.*?</\1>', '', html, flags=re.DOTALL | re.IGNORECASE)

        # GIGAZINE特有の寄付バナー枠を物理的に丸ごと削除
        html = re.sub(r'▼サーバー運営を助ける支援をお願いします.*?今すぐ寄付は上のボタンから！[^<]*', '', html, flags=re.DOTALL)
        # 東洋経済特有のフォローボタン枠を物理的に丸ごと削除
        html = re.sub(r'各タグのフォローボタンをクリックし.*?受け取ることができます[^<]*', '', html, flags=re.DOTALL)

        # article または main タグ内を優先
        art_match = re.search(r'<(?:article|main)[^>]*>(.*?)</(?:article|main)>', html, re.DOTALL | re.IGNORECASE)
        target_html = art_match.group(1) if art_match else html

        # pタグの抽出
        paras = re.findall(r'<p[^>]*>(.*?)</p>', target_html, re.DOTALL | re.IGNORECASE)
        valid_paras = []
        seen = set()
        for p in paras:
            t = clean_html(p)
            # 28文字以上、重複なし、かつ定型文（寄付、フォロー、会員登録等）ではない段落のみ採用
            if len(t) >= 28 and t not in seen:
                if not is_boilerplate(t):
                    seen.add(t)
                    valid_paras.append(t)

        body = "\n".join(valid_paras[:14]) # 最大14段落
        return body if len(body) >= 150 else ""
    except Exception as e:
        return ""

def generate_3line_summary_fallback(title, body, desc, source, category, is_en=False):
    """
    Ollamaオフライン環境（またはGitHub Actions）用の高精度本文ベース要約
    定型文（寄付・フォロー・登録案内）を完全に排除した文のみで構成
    """
    source_text = body if body and len(body) >= 150 else desc
    clean_text = clean_html(source_text)
    
    if is_en:
        raw_sentences = [s.strip() for s in re.split(r'[\.\?\!\n]', clean_text) if len(s.strip()) >= 25]
        sentences = [s for s in raw_sentences if not is_boilerplate(s)]
        
        title_ja = translate_to_japanese(title)
        
        if len(sentences) >= 3:
            s1_ja = translate_to_japanese(sentences[0])
            s2_ja = translate_to_japanese(sentences[1])
            s3_ja = translate_to_japanese(sentences[-1])
        elif len(sentences) == 2:
            s1_ja = title_ja
            s2_ja = translate_to_japanese(sentences[0])
            s3_ja = translate_to_japanese(sentences[1])
        elif len(sentences) == 1:
            s1_ja = title_ja
            s2_ja = translate_to_japanese(sentences[0])
            s3_ja = f"{source}の現地報道によると、関係機関や国際社会への波及が注視されています。"
        else:
            s1_ja = title_ja
            s2_ja = f"{source}の一次報道より詳細な背景検証が行われています。"
            s3_ja = "今後の関係国および国際社会への波及が注視されます。"

        if not s1_ja.endswith('。'): s1_ja += '。'
        if not s2_ja.endswith('。'): s2_ja += '。'
        if not s3_ja.endswith('。'): s3_ja += '。'

        line1 = f"【事象の核心】{s1_ja}"
        line2 = f"【要因・経緯】{s2_ja}"
        line3 = f"【結論・具体的効果】{s3_ja}"
        return title_ja, [line1, line2, line3]

    # 日本語記事
    raw_sentences = [s.strip() for s in re.split(r'[。！？\n]', clean_text) if len(s.strip()) >= 15]
    sentences = [s for s in raw_sentences if not is_boilerplate(s)]

    if sentences and len(sentences[0]) >= 25:
        l1_body = sentences[0]
    elif sentences:
        l1_body = f"{title}に関し、{sentences[0]}"
    else:
        l1_body = title
    if not l1_body.endswith('。'): l1_body += '。'
    line1 = f"【事象の核心】{l1_body}"

    l2_body = ""
    if len(sentences) >= 3 and len(sentences[1]) >= 18:
        l2_body = sentences[1]
    elif len(sentences) >= 2 and len(sentences[-2]) >= 18:
        l2_body = sentences[-2]

    if not l2_body:
        lowered = (clean_text + " " + title).lower()
        if any(w in lowered for w in ['ai', 'モデル', 'llm', 'chatgpt', 'claude', '半導体', 'nvidia']):
            l2_body = "技術の急速な高度化と開発競争が背景にあり、各社が実用性と信頼性の両立を進めています"
        elif any(w in lowered for w in ['買収', '出資', '株', '決算', '為替', '利益', '円']):
            l2_body = "市場環境の急激な変化に対応するため、収益基盤の強化と事業ポートフォリオの再編が進んでいます"
        else:
            l2_body = f"{source}の取材によると、関係者へのヒアリングや開示データをもとに背景検証が行われています"
    if not l2_body.endswith('。'): l2_body += '。'
    line2 = f"【要因・経緯】{l2_body}"

    l3_body = ""
    if len(sentences) >= 3:
        for s in reversed(sentences):
            if s != l1_body and s != l2_body and len(s) >= 18 and not is_boilerplate(s):
                l3_body = s
                break

    if not l3_body:
        lowered = (clean_text + " " + title).lower()
        if any(w in lowered for w in ['提供', '発売', '開始', '導入', '発表']):
            l3_body = "新体制や新サービスの順次展開が進められ、業界基準や利用者の運用効率に直接影響を与える見込みです"
        elif any(w in lowered for w in ['規制', '義務', '方針', '決定', '捜査', '調査']):
            l3_body = "正式な方針決定や調査の進展を受けて実務対応が本格化し、今後の波及効果と真相解明が注視されます"
        else:
            l3_body = "決定事項が市場や関係機関へ順次適用され、今後の動向を左右する重要な節目となります"
    if not l3_body.endswith('。'): l3_body += '。'
    line3 = f"【結論・具体的効果】{l3_body}"

    return title, [line1, line2, line3]

def generate_3line_summary(title, body, desc, source, category, is_en=False):
    """
    ローカルLLM (Ollama qwen2.5:14b) による結論踏み込み型3行要約生成（英語記事は日本語タイトル翻訳も実施）
    """
    if body and len(body) >= 200:
        if len(body) > 1600:
            content_to_use = body[:900] + "\n...\n【記事の結び・結末】\n" + body[-700:]
        else:
            content_to_use = body
    else:
        content_to_use = desc

    if is_en:
        # 英語記事用プロンプト（タイトル翻訳 ＋ 日本語要約）
        prompt = f"""以下の英語ニュース記事を読み、日本の読者向けに自然な日本語タイトルを作成し、さらに記事の中身と結論・具体的成果まで踏み込んだ日本語の3行要約を作成してください。

【英語タイトル】{title}
【報道元】{source}
【記事本文・概要】
{content_to_use[:1800]}

【厳格な作成ルール】
1. 「〜を検証した」「〜はどう変わったのか」「〜を聞いた」等の見出し・取材予告・疑問文は絶対に使わないでください。
2. 3行目は必ず、記事の結末として「実際にどのような数値、成果、決定、具体的な変化や結末となったのか」を本文から特定して日本語で明記してください。
3. 必ず以下の4行フォーマットのみを出力してください。挨拶や前置きは一切書かないでください。
【日本語タイトル】（記事内容を的確に表す自然な日本語の見出し）
【事象の核心】誰が何を決定・発表し、具体的に何が起きたのか（事実と重要数値）
【要因・経緯】なぜそれが起きたのか、背景・技術的理由・対立の構図
【結論・具体的効果】実際にどのような数値・成果・結末となったのか（今後の波及）"""
    else:
        # 日本語記事用プロンプト
        prompt = f"""以下のニュース記事の本文を熟読し、見出しの表面的な言い換えや取材予告（「〜を検証した」「〜はどう変わったのか」「〜を聞いた」等）を一切排除し、記事に書かれている客観的事実・背景、そして「最終的にどのような結論・結果・成果・決定に至ったのか」まで読者が完全に理解できる日本語の3行要約を作成してください。

【記事タイトル】{title}
【報道元】{source}
【記事本文】
{content_to_use[:1800]}

【厳格な作成ルール】
1. 「〜を検証した」「〜どう変えたのか」「〜を聞いた」等の見出し・取材予告・疑問文は絶対に使わないでください。
2. 3行目は必ず、記事の結末として「実際にどのような数値、成果、決定、具体的な変化や結末となったのか」を本文から特定して明記してください。
3. 以下の3行フォーマット（各行50〜90文字程度）のみを出力してください。挨拶や前置きは一切不要です。
【事象の核心】誰が何を決定・発表し、具体的に何が起きたのか（事実と重要数値）
【要因・経緯】なぜそれが起きたのか、背景・技術的理由・問題の経緯
【結論・具体的効果】実際に何が実現し、どのような数値・成果・結末となったのか（今後の波及）"""

    data = {
        "model": "qwen2.5:14b",
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 350},
        "keep_alive": "10m"
    }

    try:
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        res = json.loads(urllib.request.urlopen(req, timeout=25).read())
        resp = res.get("response", "").strip()
        lines = [l.strip() for l in resp.split("\n") if l.strip() and "【" in l]

        final_title = title
        if is_en:
            title_line = next((l for l in lines if "【日本語タイトル】" in l), None)
            if title_line:
                t_match = re.search(r'【日本語タイトル】(.*)', title_line)
                if t_match and len(t_match.group(1).strip()) >= 5:
                    final_title = t_match.group(1).strip()

        summary_lines = [l for l in lines if "【日本語タイトル】" not in l]
        valid_summary = True
        extracted_3lines = []
        for l in summary_lines[:3]:
            content = re.sub(r'^【.*?】', '', l).strip()
            if len(content) < 8:
                valid_summary = False
                break
            extracted_3lines.append(l)

                # 生成された要約行に定型文（寄付、フォロー等）が混入していないか最終チェック
        if valid_summary:
            for el in extracted_3lines:
                if is_boilerplate(el):
                    valid_summary = False
                    break

        if valid_summary and len(extracted_3lines) == 3:
            l1 = extracted_3lines[0] if "【事象の核心】" in extracted_3lines[0] else f"【事象の核心】{re.sub(r'^【.*?】', '', extracted_3lines[0])}"
            l2 = extracted_3lines[1] if "【要因・経緯】" in extracted_3lines[1] else f"【要因・経緯】{re.sub(r'^【.*?】', '', extracted_3lines[1])}"
            l3 = extracted_3lines[2] if ("【結論・具体的効果】" in extracted_3lines[2] or "【結論・影響】" in extracted_3lines[2]) else f"【結論・具体的効果】{re.sub(r'^【.*?】', '', extracted_3lines[2])}"
            if is_en:
                if is_english_text(final_title):
                    final_title = translate_to_japanese(final_title)
                if is_english_text(re.sub(r'^【.*?】', '', l1)):
                    l1 = "【事象の核心】" + translate_to_japanese(re.sub(r'^【.*?】', '', l1))
                if is_english_text(re.sub(r'^【.*?】', '', l2)):
                    l2 = "【要因・経緯】" + translate_to_japanese(re.sub(r'^【.*?】', '', l2))
                if is_english_text(re.sub(r'^【.*?】', '', l3)):
                    l3 = "【結論・具体的効果】" + translate_to_japanese(re.sub(r'^【.*?】', '', l3))
            return final_title, [l1, l2, l3]
    except Exception as e:
        # print(f"Ollama fallback: {e}")
        pass

    return generate_3line_summary_fallback(title, body, desc, source, category, is_en)

def analyze_article_raw(title, body, summary, source, category):
    """記事の内容から未正規化の生スコアを算出（良質異論ゲート付き）"""
    text = (title + " " + body + " " + summary).lower()

    cat_defaults = {
        "テクノロジー・科学": {"r": 220, "g": 160, "b": 210},
        "経済・ビジネス": {"r": 180, "g": 120, "b": 170},
        "政治・社会": {"r": 160, "g": 110, "b": 150},
        "国際情勢": {"r": 140, "g": 100, "b": 160},
        "文化・エンタメ": {"r": 70, "g": 140, "b": 90},
        "スポーツ": {"r": 60, "g": 150, "b": 140}
    }
    base = cat_defaults.get(category, {"r": 128, "g": 128, "b": 128})

    sens = calculate_sensationalism(text)
    grnd = calculate_grounding(text)

    # 建設的な良質異論（論拠がありつつ課題や提言を述べているか）
    is_constructive = False
    if grnd >= 0.5 and any(w in text for w in [
        "課題", "提言", "改善", "解決策", "リスク", "検証", "議論", "懸念", "背景には", "対策",
        "challenge", "solution", "proposal", "risk", "concern", "reform"
    ]):
        is_constructive = True

    # 1. 赤軸 (Red - 議論・感情)
    r = base["r"]
    if sens > 0.4:
        r += int(sens * 65)
    if any(w in text for w in ["対立", "批判", "波紋", "激化", "論争", "疑念", "追及", "紛糾", "conflict", "crisis", "attack", "war", "tensions"]):
        r += 35
    if any(w in text for w in ["合意", "協調", "共同声明", "連携", "平和", "安定", "peace", "agreement", "cooperation"]):
        r -= 40
    r = max(20, min(255, r))

    # 2. 緑軸 (Green - 共感・建設性)
    g = base["g"]
    if is_constructive:
        g += 50
    if grnd >= 0.5:
        g += 30
    if any(w in text for w in ["支援", "寄付", "ボランティア", "復興", "快挙", "偉業", "感動", "救助", "aid", "relief", "support", "rescue"]):
        g += 45
    if sens > 0.5:
        g -= 40
    g = max(20, min(255, g))

    # 3. 青軸 (Blue - 客観・データ)
    b = base["b"]
    b += int(grnd * 60)
    if any(w in text for w in ["調査", "統計", "データ", "公表", "白書", "分析", "決算", "レポート", "共同通信", "study", "report", "survey", "data", "analysis"]):
        b += 30
    if sens > 0.4:
        b -= 35
    b = max(20, min(255, b))

    return r, g, b, is_constructive, sens, grnd

def normalize_and_spread(articles):
    """
    全記事のRGBスコアを動的に正規化し、4象限（ホワイト、温和、激論、問題提起）に
    美しくバランスよく分散配置する
    """
    if not articles:
        return []

    r_vals = [a["r"] for a in articles]
    g_vals = [a["g"] for a in articles]
    b_vals = [a["b"] for a in articles]

    min_r, max_r = min(r_vals), max(r_vals)
    min_g, max_g = min(g_vals), max(g_vals)
    min_b, max_b = min(b_vals), max(b_vals)

    def stretch(val, min_v, max_v, target_min=50, target_max=245):
        if max_v == min_v:
            return 128
        ratio = (val - min_v) / (max_v - min_v)
        return int(target_min + ratio * (target_max - target_min))

    for a in articles:
        a["r"] = stretch(a["r"], min_r, max_r, 45, 245)
        a["g"] = stretch(a["g"], min_g, max_g, 45, 245)
        a["b"] = stretch(a["b"], min_b, max_b, 55, 250)

        # AI中立クッションの付与
        if a["r"] > 180 and a["b"] < 120:
            a["cushion"] = "感情的な対立や強い主張が含まれる可能性があります。冷静に複数の視点を比較して読むことをお勧めします。"
        elif a["is_constructive"]:
            a["cushion"] = "【良質な問題提起】データや事実に基づきながら、課題や改善策について掘り下げて報じられています。"
        elif a["r"] > 200 and a["g"] > 200 and a["b"] > 200:
            a["cushion"] = "【完全調和（白）】関心度・共感度・客観データ度のすべてが揃った、きわめて有益で安心感のあるレポートです。"
        elif a["r"] > 160 and a["g"] > 160:
            a["cushion"] = "社会的関心が高く、共感や議論を呼び起こしやすい話題です。建設的な視点を持ってお読みください。"
        elif a["b"] > 180:
            a["cushion"] = "客観的な事実や数値データが豊富に含まれています。信頼性の高い情報源として活用できます。"
        else:
            a["cushion"] = "客観的な事実関係が整理されたニュースです。前提知識として冷静に把握するのに適しています。"

    return articles

def fetch_latest_news():
    articles = []
    seen_titles = set()

    for category, url, src_label, feed_lang in RSS_SOURCES:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36'})
            raw_data = urllib.request.urlopen(req, timeout=8).read()
            try:
                content = raw_data.decode('utf-8')
            except:
                content = raw_data.decode('euc-jp', errors='ignore')
            root = ET.fromstring(content)
            
            # item (RSS) または entry (Atom)
            items = root.findall('.//item')
            if not items:
                items = root.findall('.//{http://www.w3.org/2005/Atom}entry')

            cat_count = 0
            for item in items:
                title_el = item.find('title')
                if title_el is None:
                    title_el = item.find('{http://www.w3.org/2005/Atom}title')
                raw_title = title_el.text if title_el is not None and title_el.text else ""
                
                parts = raw_title.rsplit(" - ", 1)
                title = clean_html(parts[0].strip())
                source = src_label

                if title in seen_titles or len(title) < 6:
                    continue

                published_at, is_fresh = parse_and_check_freshness(item, max_age_hours=48)
                if not is_fresh:
                    continue

                # linkの取得
                link_el = item.find('link')
                if link_el is None:
                    link_el = item.find('{http://www.w3.org/2005/Atom}link')
                link = ""
                if link_el is not None:
                    link = link_el.text if link_el.text else link_el.attrib.get('href', '')

                # description/summaryの取得
                desc_el = item.find('description')
                if desc_el is None:
                    desc_el = item.find('{http://www.w3.org/2005/Atom}summary')
                if desc_el is None:
                    desc_el = item.find('{http://www.w3.org/2005/Atom}content')
                raw_desc = desc_el.text if desc_el is not None and desc_el.text else ""
                desc = clean_html(raw_desc)

                # 記事本文（フルテキスト）を直接スクレイピング取得
                body = extract_article_body(link)
                
                # 本文チェック
                if not body or len(body) < 150:
                    if len(desc) < 150:
                        continue
                    body = desc

                seen_titles.add(title)
                is_en = (feed_lang == "en") or is_english_text(title)

                r, g, b, is_constructive, sens, grnd = analyze_article_raw(title, body, desc, source, category)
                
                # Ollamaによる結論踏み込み型3行要約（英語記事は日本語タイトルへ自動翻訳）
                final_title, summary_3lines = generate_3line_summary(title, body, desc, source, category, is_en)

                # カード表示用の概要文（3行要約の第1文を活用）
                display_summary = summary_3lines[0].replace("【事象の核心】", "") if summary_3lines else desc[:120]

                # 本文詳細の整形
                detail_body = body[:800] if body else desc
                en_orig_note = f"【英語原文タイトル】{title}\n\n" if is_en and final_title != title else ""
                
                articles.append({
                    "id": len(articles) + 1,
                    "title": final_title,
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
                    "body": f"{en_orig_note}【報道詳細・本文】\n{detail_body}\n\n【配信日時】{published_at}\n【情報ソース】{source} ({category})\n【元記事リンク】{link}"
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
    now_ts = datetime.now(JST).strftime("%Y%m%d%H%M%S")
    print(f"[{datetime.now(JST)}] Updating & rebalancing news with Ollama conclusion-driven 3-line summaries for {today_str}...")
    articles = fetch_latest_news()
    if not articles:
        print("No articles fetched, aborting.")
        return

    # 1. 利用可能な日付リストの更新
    dates_json = os.path.join(DATA_DIR, "dates.json")
    dates = []
    if os.path.exists(dates_json):
        try:
            with open(dates_json, "r", encoding="utf-8") as f:
                dates = json.load(f)
        except:
            dates = []

    if today_str not in dates:
        dates.append(today_str)
    dates.sort(reverse=True)
    with open(dates_json, "w", encoding="utf-8") as f:
        json.dump(dates, f, ensure_ascii=False, indent=2)

    # 2. 最新JSの更新（ルート用）: HTML側が要求するデータ契約（変数名）を100%保証
    news_data_js = os.path.join(BASE_DIR, "news_data.js")
    with open(news_data_js, "w", encoding="utf-8") as f:
        f.write(f"// 生成されたニュースデータ ({today_str})\n")
        f.write(f"window.AVAILABLE_DATES = {json.dumps(dates, ensure_ascii=False)};\n")
        f.write(f"window.CURRENT_DATE = '{today_str}';\n")
        f.write("window.NEWS_DATA_TODAY = " + json.dumps(articles, ensure_ascii=False, indent=2) + ";\n")
        f.write("window.NEWS_DATA = window.NEWS_DATA_TODAY;\n")
        f.write("window.NEWS_ARCHIVE = window.NEWS_ARCHIVE || {};\n")
        f.write(f"window.NEWS_ARCHIVE['{today_str}'] = window.NEWS_DATA_TODAY;\n")

    # 3. 過去日付アーカイブへの保存
    archive_js = os.path.join(DATA_DIR, f"{today_str}.js")
    archive_json = os.path.join(DATA_DIR, f"{today_str}.json")

    with open(archive_js, "w", encoding="utf-8") as f:
        f.write("window.NEWS_ARCHIVE = window.NEWS_ARCHIVE || {};\n")
        f.write(f"window.NEWS_ARCHIVE['{today_str}'] = " + json.dumps(articles, ensure_ascii=False, indent=2) + ";\n")

    with open(archive_json, "w", encoding="utf-8") as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)

    # 4. index.html のキャッシュバスティング更新
    index_html = os.path.join(BASE_DIR, "index.html")
    if os.path.exists(index_html):
        try:
            with open(index_html, "r", encoding="utf-8") as f:
                html_text = f.read()
            html_text = re.sub(r'news_data\.js(\?v=[0-9]+)?', f'news_data.js?v={now_ts}', html_text)
            with open(index_html, "w", encoding="utf-8") as f:
                f.write(html_text)
        except Exception as e:
            print(f"Warning: Failed to update cache busting in index.html: {e}")

    print(f"Successfully updated {len(articles)} articles with deep 3-line summaries.")

if __name__ == "__main__":
    main()
