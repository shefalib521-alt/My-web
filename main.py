# -*- coding: utf-8 -*-
# Advanced automated Bengali blogging engine for GitHub Actions.
#
# Each run:
#   1. picks a random category + topic + hook style
#   2. generates an SEO-friendly Bengali article with Groq (quality-validated)
#   3. commits posts/YYYYMMDD_HHMMSS.md (with front matter and featured image)
#   4. rebuilds sitemap.xml and rss.xml in the repository root
#
# Required env vars : GROQ_API_KEY, GH_TOKEN, GH_OWNER, GH_REPO
# Optional env vars : GH_BRANCH, SITE_URL, SITE_TITLE, SITE_DESCRIPTION,
#                     POST_EXT (default .html), POST_LAYOUT,
#                     BLOG_TOPIC, BLOG_CATEGORY (1-6)
# Only dependency   : requests. No input() anywhere, so it is safe in CI.

import base64
import json
import os
import random
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from urllib.parse import quote
from xml.sax.saxutils import escape as xml_escape

import requests

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
GITHUB_API = "https://api.github.com"
IMAGE_API = "https://image.pollinations.ai/prompt/"

PREFERRED_MODELS = [
    "llama-3.3-70b-versatile",
    "llama3-70b-8192",
    "openai/gpt-oss-120b",
    "llama-3.1-70b-versatile",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "qwen/qwen3-32b",
]
MIN_MODEL_B = 30
NON_CHAT_KEYWORDS = ["whisper", "tts", "guard", "playai", "embed", "orpheus", "compound"]
MAX_ATTEMPTS_PER_MODEL = 3
HTTP_TIMEOUT = 40
LLM_TIMEOUT = 150
IMAGE_TIMEOUT = 60
RSS_MAX_ITEMS = 20

DEFAULT_SITE_TITLE = "বাংলা বিজ্ঞান ও কৌতূহল"
DEFAULT_SITE_DESCRIPTION = "প্রতিদিন নতুন নতুন বিজ্ঞান, প্রযুক্তি, রহস্য আর গল্প - সহজ বাংলায়।"

# ----------------------------------------------------------------------
# Content plan: categories, topics, hook styles
# ----------------------------------------------------------------------
CATEGORIES = [
    {
        "name": "বিজ্ঞান ও আগামী দিনের প্রযুক্তি",
        "tone": "Curious, inspiring and forward-looking. Precise but simple. Make the reader feel the future is close.",
        "image_hint": "futuristic science technology laboratory space",
        "topics": [
            "কোয়ান্টাম কম্পিউটার: যে যন্ত্র একসঙ্গে অনেক সম্ভাবনা নিয়ে ভাবতে পারে",
            "জেমস ওয়েব স্পেস টেলিস্কোপ মহাবিশ্বের কোন রহস্য খুঁজে বেড়াচ্ছে",
            "কৃত্রিম বুদ্ধিমত্তা কীভাবে শেখে: মেশিন লার্নিংয়ের সহজ গল্প",
            "নিউক্লিয়ার ফিউশন: পৃথিবীতেই ছোট একটি সূর্য বানানোর স্বপ্ন",
            "মঙ্গল গ্রহে মানুষের বসতি গড়া কি সত্যিই সম্ভব",
            "ব্রেন-কম্পিউটার ইন্টারফেস: মনের ভাবনায় যন্ত্র চালানোর প্রযুক্তি",
            "স্পেস এলিভেটর: পৃথিবী থেকে মহাকাশে লিফটে চড়ার কল্পনা",
            "ভবিষ্যতের শহর: স্মার্ট সিটি, সৌরশক্তি আর স্বয়ংচালিত গাড়ি",
        ],
    },
    {
        "name": "অদ্ভুত ও রহস্যময় বিজ্ঞান",
        "tone": "Mysterious, atmospheric and suspenseful, but always explained with real, well-established science. No pseudoscience.",
        "image_hint": "mysterious cosmos nebula dark atmospheric nature",
        "topics": [
            "ব্ল্যাক হোলের ভেতরে ঢুকলে কী ঘটতে পারে",
            "ডার্ক ম্যাটার: মহাবিশ্বের অদৃশ্য আঠা",
            "বল লাইটনিং: আকাশের রহস্যময় আগুনের গোলা",
            "বারমুডা ট্রায়াঙ্গেলের রহস্যের বিজ্ঞানসম্মত ব্যাখ্যা",
            "পৃথিবীর সবচেয়ে অদ্ভুত প্রাণী যাদের দেখলে বিশ্বাস হতে চায় না",
            "সময় কি সত্যিই ধীরে চলে: আইনস্টাইনের টাইম ডায়ালেশন",
            "গোলাপি রঙের হ্রদ: প্রকৃতির বিস্ময়কর রঙের খেলা",
            "মহাসাগরের গভীরতম অন্ধকারে কী লুকিয়ে আছে",
        ],
    },
    {
        "name": "মজাদার ও কৌতূহলোদ্দীপক বিজ্ঞান",
        "tone": "Playful, witty and warm, full of humor and everyday curiosity. Like a funny friend explaining something cool.",
        "image_hint": "colorful everyday science curiosity fun experiment",
        "topics": [
            "পেঁয়াজ কাটলে চোখে জল আসে কেন",
            "হাই তুললে অন্যদেরও হাই ওঠে কেন",
            "বৃষ্টির পর মাটির সোঁদা গন্ধ আসে কেন",
            "ঘুমের মধ্যে আমরা স্বপ্ন দেখি কেন",
            "ঝাল খেলে জিভ জ্বলে কেন",
            "আয়নায় ডান-বাম উল্টে যায় কিন্তু ওপর-নিচ উল্টায় না কেন",
            "পপকর্ন ফুলে ওঠে কীভাবে",
            "মশা কেন কানের কাছে ভনভন করে",
        ],
    },
    {
        "name": "গল্পের ছলে শেখা",
        "tone": "Story-driven, dramatic and emotional, with a beginning, a struggle and a turning point. Narrate like a gifted storyteller.",
        "image_hint": "vintage inventor workshop historical discovery dramatic light",
        "topics": [
            "থমাস এডিসন ও বাল্ব আবিষ্কারের হাজারবার চেষ্টার গল্প",
            "মারি কুরি: যিনি অন্ধকারে আলো খুঁজে পেয়েছিলেন",
            "রাইট ভাইদের আকাশ জয়ের রোমাঞ্চকর গল্প",
            "জগদীশচন্দ্র বসু: গাছেরও সাড়া আছে প্রমাণ করা বাঙালি বিজ্ঞানী",
            "আলেকজান্ডার ফ্লেমিং ও পেনিসিলিন আবিষ্কারের আকস্মিক গল্প",
            "অ্যাপোলো ১১: চাঁদে প্রথম পদচিহ্নের পেছনের গল্প",
            "নিকোলা টেসলা: যে প্রতিভাকে পৃথিবী দেরিতে চিনেছে",
            "সত্যেন্দ্রনাথ বসু ও বোসন কণার অজানা গল্প",
        ],
    },
    {
        "name": "শিশু ও নতুনদের জন্য সহজ বিজ্ঞান",
        "tone": "Very simple, cheerful and friendly, using short sentences, tiny examples and playful comparisons that a 10-year-old enjoys.",
        "image_hint": "colorful cartoon style kids science friendly bright",
        "topics": [
            "বজ্রপাত কীভাবে হয়: ছোটদের জন্য সহজ ব্যাখ্যা",
            "রংধনু কীভাবে তৈরি হয়",
            "আকাশ কেন নীল দেখায়",
            "চাঁদের আকার রোজ বদলায় কেন",
            "গাছ কীভাবে নিজের খাবার নিজে তৈরি করে",
            "সূর্য কী দিয়ে তৈরি এবং কেন এত গরম",
            "কম্পিউটার কীভাবে ভাবে: ছোটদের জন্য সহজ গল্প",
            "ভূমিকম্প কেন হয় এবং তখন কী করতে হয়",
        ],
    },
    {
        "name": "অজানা রোমাঞ্চকর তথ্য",
        "tone": "Energetic, mind-blowing and punchy. Every section should make the reader say 'wow, I did not know that!'.",
        "image_hint": "amazing facts wonder universe nature surprise",
        "topics": [
            "মধু কেন কখনো নষ্ট হয় না",
            "অক্টোপাসের তিনটি হৃদয় কেন",
            "শুক্র গ্রহে একটি দিন এক বছরের চেয়ে বড় কেন",
            "পৃথিবীর সবচেয়ে প্রাচীন গাছেরা",
            "আমাদের শরীরে লুকিয়ে থাকা বিস্ময়কর তথ্য",
            "মহাকাশে গেলে মানুষের শরীরে কী কী বদল ঘটে",
            "কাগজ, কম্পাস, বারুদ ও ছাপাখানা: প্রাচীন আবিষ্কার যা দুনিয়া বদলে দিয়েছে",
            "পিঁপড়ার অবিশ্বাস্য সমাজ: ছোট্ট প্রাণীর বিশাল রহস্য",
        ],
    },
]

HOOK_ANGLES = [
    "Open with a surprising question the reader cannot ignore.",
    "Open with a vivid 3-sentence mini-story set in a Bangladeshi village, town or home.",
    "Open with one mind-blowing fact, then promise to explain how it is possible.",
    "Open with a playful 'what if' imaginary scenario.",
    "Open with a short friendly conversation between a curious child and a grandparent.",
    "Open with a relatable everyday moment (tea, rain, load-shedding, a bus ride) that leads into the topic.",
]

BN_CHAR = re.compile(r"[\u0980-\u09FF]")
LATIN_CHAR = re.compile(r"[A-Za-z]")
FOREIGN_SCRIPT = re.compile(
    r"[\u0400-\u04FF\u0600-\u06FF\u0900-\u0963\u0966-\u097F\u3040-\u30FF\u4E00-\u9FFF\uAC00-\uD7AF]"
)
STEM_PATTERN = re.compile(r"^(\d{8}_\d{6})\.md$")


class ConfigError(Exception):
    pass


class FatalError(Exception):
    pass


def log(level, message):
    print("[" + level + "] " + message, flush=True)


# ----------------------------------------------------------------------
# Environment, time, URLs
# ----------------------------------------------------------------------
def load_config():
    values = {}
    missing = []
    for name in ["GROQ_API_KEY", "GH_TOKEN", "GH_OWNER", "GH_REPO"]:
        value = os.environ.get(name, "").strip()
        if value:
            values[name] = value
        else:
            missing.append(name)
    if missing:
        raise ConfigError(
            "Missing required environment variable(s): " + ", ".join(missing)
            + ". Add them as GitHub Secrets and pass them in the workflow 'env:' block."
        )
    values["GH_BRANCH"] = os.environ.get("GH_BRANCH", "").strip()
    values["SITE_URL"] = os.environ.get("SITE_URL", "").strip()
    values["SITE_TITLE"] = os.environ.get("SITE_TITLE", "").strip() or DEFAULT_SITE_TITLE
    values["SITE_DESCRIPTION"] = os.environ.get("SITE_DESCRIPTION", "").strip() or DEFAULT_SITE_DESCRIPTION
    values["POST_LAYOUT"] = os.environ.get("POST_LAYOUT", "").strip()
    values["BLOG_TOPIC"] = os.environ.get("BLOG_TOPIC", "").strip()
    values["BLOG_CATEGORY"] = os.environ.get("BLOG_CATEGORY", "").strip()
    if "POST_EXT" in os.environ:
        values["POST_EXT"] = os.environ.get("POST_EXT", "").strip()
    else:
        values["POST_EXT"] = ".html"
    return values


def now_dhaka():
    if ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo("Asia/Dhaka"))
        except Exception:
            pass
    return datetime.now(timezone(timedelta(hours=6)))


def site_base(cfg):
    if cfg["SITE_URL"]:
        return cfg["SITE_URL"].rstrip("/")
    owner = cfg["GH_OWNER"]
    repo = cfg["GH_REPO"]
    if repo.lower() == (owner + ".github.io").lower():
        return "https://" + owner + ".github.io"
    return "https://" + owner + ".github.io/" + repo


def post_url(cfg, stem):
    return site_base(cfg) + "/posts/" + stem + cfg["POST_EXT"]


def err_message(resp):
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:300]
    if isinstance(data, dict):
        e = data.get("error", data.get("message"))
        if isinstance(e, dict):
            return str(e.get("message", e))
        return str(e)
    return resp.text[:300]


# ----------------------------------------------------------------------
# Groq: model discovery
# ----------------------------------------------------------------------
def groq_headers(cfg):
    return {
        "Authorization": "Bearer " + cfg["GROQ_API_KEY"],
        "Content-Type": "application/json",
    }


def model_size_b(model_id):
    nums = re.findall(r"(\d+(?:\.\d+)?)b", model_id.lower())
    if not nums:
        return 0.0
    return max(float(n) for n in nums)


def is_chat_model(model_id):
    low = model_id.lower()
    for word in NON_CHAT_KEYWORDS:
        if word in low:
            return False
    return True


def discover_models(cfg):
    try:
        r = requests.get(GROQ_MODELS_URL, headers=groq_headers(cfg), timeout=HTTP_TIMEOUT)
        if r.status_code == 401:
            raise FatalError("GROQ_API_KEY was rejected by Groq (HTTP 401).")
        r.raise_for_status()
        available = []
        for m in r.json().get("data", []):
            mid = m.get("id", "")
            if mid and m.get("active") is not False and is_chat_model(mid):
                available.append(mid)
    except FatalError:
        raise
    except (requests.exceptions.RequestException, ValueError) as e:
        log("WARN", "Could not fetch Groq model list (" + str(e) + "). Using the built-in list.")
        return list(PREFERRED_MODELS)

    ranked = [m for m in PREFERRED_MODELS if m in available]
    extras = [m for m in available if m not in ranked and model_size_b(m) >= MIN_MODEL_B]
    extras.sort(key=lambda m: -model_size_b(m))
    ranked = ranked + extras
    if not ranked:
        raise FatalError(
            "No large Bengali-capable model is active on this Groq account. Active models: "
            + (", ".join(available) or "none")
        )
    return ranked


# ----------------------------------------------------------------------
# Groq: prompt
# ----------------------------------------------------------------------
SYSTEM_PROMPT = " ".join([
    "You are an award-winning Bengali (Bangla) writer and science communicator who publishes a hugely popular blog in Bangladesh.",
    "You write like a gifted human storyteller, never like a textbook or a robot.",
    "Your Bengali is natural, warm, vivid and culturally authentic (spoken-literary, চলিত ভাষা), with perfect spelling.",
    "You vary sentence rhythm, use concrete images and humor where suitable, and you avoid cliches and filler.",
    "You never invent statistics, dates, names or quotes. You only state well-established facts and hedge honestly when unsure.",
    "You never present pseudoscience or conspiracy theories as truth.",
])


def build_user_prompt(category, topic, angle):
    lines = [
        "Write a world-class Bengali blog post.",
        "Category: " + category["name"],
        "Topic: " + topic,
        "Tone: " + category["tone"],
        "Hook style: " + angle,
        "",
        "Output EXACTLY this marker format. Each marker must be alone on its own line:",
        "[[DESCRIPTION]]",
        "SEO meta description in Bengali, 100 to 155 characters, curiosity-driven, no quotation marks.",
        "[[TAGS]]",
        "4 to 6 Bengali SEO keywords separated by commas.",
        "[[IMAGE_PROMPT]]",
        "8 to 14 ENGLISH words describing a stunning cover photo for this topic (no text, no faces).",
        "[[ARTICLE]]",
        "The full article in Markdown, following the structure below.",
        "[[END]]",
        "",
        "Article structure (Markdown only, in exactly this order):",
        "1. A single H1 line starting with '# ': a catchy, click-worthy but honest Bengali title.",
        "2. Hook intro: 2 short gripping paragraphs following the hook style above. No heading for the intro.",
        "3. '## এক নজরে দ্রুত তথ্য' followed by a Markdown table: header row '| বিষয় | তথ্য |',",
        "   separator row '|---|---|', then 4 to 6 data rows with real, accurate facts.",
        "4. Three or four body sections, each starting with an H2 line '## ' with an intriguing title.",
        "   Use '### ' subheadings where helpful, **bold** highlights and bullet points starting with '- '.",
        "   Include at least one real-life analogy or example from daily life in Bangladesh.",
        "5. '## উপসংহার': a short memorable closing plus one thought-provoking question for the reader.",
        "6. '## সচরাচর জিজ্ঞাসা (FAQ)': 3 or 4 questions, each as an H3 line '### question?'",
        "   followed by a 1 to 3 sentence answer.",
        "",
        "Writing rules:",
        "- Length: 800 to 1200 words of natural, fluent Bengali.",
        "- Sound like a human storyteller. Short sentences mixed with longer ones. Concrete details.",
        "- Explain every technical term simply. No empty filler and no repeated sentences.",
        "- Safe and suitable for readers of all ages.",
        "- Use the exact headings given above for the quick facts, conclusion and FAQ sections.",
        "- No emojis. Do not wrap anything in code fences. No text outside the markers.",
    ]
    return "\n".join(lines)


# ----------------------------------------------------------------------
# Parsing and validation
# ----------------------------------------------------------------------
def parse_response(text):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I)
    parts = re.split(r"\[\[\s*([A-Z_]+)\s*\]\]", text)
    data = {}
    for i in range(1, len(parts) - 1, 2):
        data[parts[i]] = parts[i + 1].strip()
    article = data.get("ARTICLE", "")
    article = re.sub(r"^```[A-Za-z]*\s*\n", "", article.strip())
    article = re.sub(r"\n```\s*$", "", article).strip()
    data["ARTICLE"] = article
    return data


def bengali_ratio(text):
    bn = len(BN_CHAR.findall(text))
    la = len(LATIN_CHAR.findall(text))
    if bn + la == 0:
        return 0.0
    return bn / (bn + la)


def is_repetitive(text):
    words = re.findall(r"[\u0980-\u09FF]+", text)
    if len(words) < 300:
        return True, "too few Bengali words (" + str(len(words)) + ")"
    if len(set(words)) / float(len(words)) < 0.3:
        return True, "low vocabulary variety"
    grams = Counter()
    for i in range(len(words) - 3):
        grams[" ".join(words[i:i + 4])] += 1
    if grams and grams.most_common(1)[0][1] > 4:
        return True, "a phrase repeats too often"
    if re.search(r"(.)\1{6,}", text):
        return True, "character run detected"
    return False, ""


def has_heading(text, keyword_pattern):
    return re.search(r"^##[^\n#]*(" + keyword_pattern + ")", text, flags=re.M) is not None


def validate_article(text):
    first = ""
    for line in text.splitlines():
        if line.strip():
            first = line.strip()
            break
    if not re.match(r"^# [^#]", first):
        return False, "article does not start with an H1 title"
    if len(re.findall(r"^# [^#]", text, flags=re.M)) != 1:
        return False, "article must contain exactly one H1"
    if not has_heading(text, "এক নজরে"):
        return False, "missing quick facts section"
    if not has_heading(text, "উপসংহার"):
        return False, "missing conclusion section"
    if not has_heading(text, "সচরাচর জিজ্ঞাসা|FAQ"):
        return False, "missing FAQ section"
    if len(re.findall(r"^## ", text, flags=re.M)) < 5:
        return False, "fewer than 5 H2 sections"
    if len(re.findall(r"^### ", text, flags=re.M)) < 3:
        return False, "fewer than 3 H3 headings (FAQ questions)"
    table_lines = [x for x in text.splitlines() if x.strip().startswith("|")]
    separator = re.search(r"^\s*\|?\s*:?-{2,}:?\s*\|", text, flags=re.M)
    if len(table_lines) < 5 or not separator:
        return False, "quick facts table is missing or too small"
    if len(re.findall(r"^\s*[-*] ", text, flags=re.M)) < 3:
        return False, "fewer than 3 bullet points"
    if len(text) < 3000:
        return False, "article too short (" + str(len(text)) + " chars)"
    if FOREIGN_SCRIPT.search(text):
        return False, "foreign script characters found"
    if bengali_ratio(text) < 0.75:
        return False, "not enough Bengali text"
    bad, why = is_repetitive(text)
    if bad:
        return False, why
    odd_bold = 0
    for line in text.splitlines():
        if line.count("**") % 2 == 1:
            odd_bold += 1
    if odd_bold >= 3:
        return False, "broken bold markers in several lines"
    return True, ""


def extract_title(article):
    for line in article.splitlines():
        m = re.match(r"^# ([^#].*)$", line.strip())
        if m:
            return m.group(1).strip().strip("*# ")
    return ""


def derive_description(article):
    chunks = []
    for line in article.splitlines():
        s = line.strip()
        if not s:
            if chunks:
                break
            continue
        if s.startswith("#") or s.startswith("|") or s.startswith("-"):
            continue
        chunks.append(s)
    text = " ".join(chunks)
    text = re.sub(r"[*_`>]", "", text).strip()
    if len(text) > 155:
        text = text[:152].rstrip() + "..."
    return text


def build_meta(parsed, article, category):
    title = extract_title(article)
    description = re.sub(r"\s+", " ", parsed.get("DESCRIPTION", "")).strip().strip("\"'")
    if len(description) < 40 or len(description) > 200 or bengali_ratio(description) < 0.6:
        description = derive_description(article)
    tags = []
    for t in re.split(r"[,\u060C\u3001\n]", parsed.get("TAGS", "")):
        t = t.strip().strip("#*-\"' ")
        if t and len(t) <= 40 and t not in tags:
            tags.append(t)
    tags = tags[:6]
    if not tags:
        tags = [category["name"]]
    prompt = re.sub(r"[^A-Za-z0-9 ,.\-]", " ", parsed.get("IMAGE_PROMPT", ""))
    prompt = re.sub(r"\s+", " ", prompt).strip()[:160]
    if len(prompt) < 10:
        prompt = category["image_hint"]
    return {"title": title, "description": description, "tags": tags, "image_prompt": prompt}


# ----------------------------------------------------------------------
# Groq:
