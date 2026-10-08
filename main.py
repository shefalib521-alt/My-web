# -*- coding: utf-8 -*-
# Daily Bengali blog generator for GitHub Actions.
# Generates a Markdown article with Groq and commits it to posts/YYYYMMDD_HHMMSS.md
# Required env vars: GROQ_API_KEY, GH_TOKEN, GH_OWNER, GH_REPO
# Optional env vars: GH_BRANCH (default: repo default branch), BLOG_TOPIC (override topic)
# Only dependency: requests. Never uses input(), so it is safe in CI.

import base64
import os
import random
import re
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
GITHUB_API = "https://api.github.com"

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
LLM_TIMEOUT = 120

TOPICS = [
    "কৃত্রিম বুদ্ধিমত্তা (AI) কী এবং এটি কীভাবে আমাদের দৈনন্দিন জীবন বদলে দিচ্ছে",
    "চ্যাটবট কীভাবে মানুষের মতো উত্তর দেয়",
    "মোবাইলের ব্যাটারি দ্রুত শেষ হয় কেন এবং কীভাবে বেশিক্ষণ টেকানো যায়",
    "ইন্টারনেট কীভাবে কাজ করে: সহজ ভাষায় ব্যাখ্যা",
    "অনলাইনে পাসওয়ার্ড ও অ্যাকাউন্ট নিরাপদ রাখার সহজ উপায়",
    "ক্লাউড কম্পিউটিং কী এবং আমরা কীভাবে এটি ব্যবহার করি",
    "মহাকাশে মানুষের নতুন অভিযান: চাঁদ ও মঙ্গল গ্রহের গল্প",
    "জলবায়ু পরিবর্তন কী এবং বাংলাদেশের ওপর এর প্রভাব",
    "সৌরশক্তি কীভাবে বিদ্যুৎ তৈরি করে",
    "ব্লকচেইন ও ক্রিপ্টোকারেন্সি: ভয় না বুঝে সহজ ধারণা",
    "রোবটিক্স কী এবং ভবিষ্যতে রোবট কোথায় কাজ করবে",
    "ডিজিটাল ডিটক্স: স্ক্রিনের নেশা কমানোর বৈজ্ঞানিক উপায়",
    "ডিপফেক কী এবং ভুয়া ভিডিও থেকে নিজেকে বাঁচানোর উপায়",
    "ইলেকট্রিক গাড়ি কীভাবে চলে এবং এটি কি সত্যিই পরিবেশবান্ধব",
    "ফ্রিল্যান্সিংয়ের জন্য শেখার মতো ৫টি ডিজিটাল দক্ষতা",
    "৫জি নেটওয়ার্ক কী এবং এটি আমাদের জীবনে কী পরিবর্তন আনবে",
]

BN_CHAR = re.compile(r"[\u0980-\u09FF]")
LATIN_CHAR = re.compile(r"[A-Za-z]")
FOREIGN_SCRIPT = re.compile(
    r"[\u0400-\u04FF\u0600-\u06FF\u0900-\u0963\u0966-\u097F\u3040-\u30FF\u4E00-\u9FFF\uAC00-\uD7AF]"
)


class ConfigError(Exception):
    pass


class FatalError(Exception):
    pass


def log(level, message):
    print("[" + level + "] " + message, flush=True)


# ----------------------------------------------------------------------
# Environment and time
# ----------------------------------------------------------------------
def load_config():
    names = ["GROQ_API_KEY", "GH_TOKEN", "GH_OWNER", "GH_REPO"]
    values = {}
    missing = []
    for name in names:
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
    values["BLOG_TOPIC"] = os.environ.get("BLOG_TOPIC", "").strip()
    return values


def now_dhaka():
    if ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo("Asia/Dhaka"))
        except Exception:
            pass
    return datetime.now(timezone(timedelta(hours=6)))


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
# Groq: prompt, cleaning, validation
# ----------------------------------------------------------------------
SYSTEM_PROMPT = " ".join([
    "You are a senior Bengali (Bangla) tech and science writer for a popular blog in Bangladesh.",
    "You write clear, simple, fluent, natural Bengali for ordinary readers and beginners.",
    "You explain every technical term with a real-life analogy from daily life in Bangladesh.",
    "Your spelling is perfect and you never repeat sentences or phrases.",
    "You never invent statistics, dates, names or quotes; if unsure, stay general and honest.",
])


def build_user_prompt(topic):
    lines = [
        "Write a complete, engaging blog post in Bengali on this topic: " + topic,
        "",
        "Formatting rules (valid Markdown only):",
        "- Start with exactly one H1 title line: '# ...' (a catchy Bengali title).",
        "- Then a short friendly introduction paragraph (2-3 sentences).",
        "- Then 4 or 5 sections, each starting with an H2 line: '## ...'.",
        "- Inside sections use short paragraphs and bullet points starting with '- '.",
        "- Use **bold** for key words. Include at least one real-life analogy.",
        "- The last section must be '## উপসংহার' with a practical, encouraging takeaway.",
        "- Do NOT wrap the answer in code fences. Do NOT add any text outside the article.",
        "- Do NOT use emojis in headings.",
        "",
        "Quality rules: about 600-900 words, natural friendly Bengali, correct spelling,",
        "English words only when truly necessary (for example AI, Wi-Fi) and explained. No repetition.",
    ]
    return "\n".join(lines)


def clean_markdown(text):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I)
    text = text.strip()
    text = re.sub(r"^```[A-Za-z]*\s*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text)
    return text.strip()


def bengali_ratio(text):
    bn = len(BN_CHAR.findall(text))
    la = len(LATIN_CHAR.findall(text))
    if bn + la == 0:
        return 0.0
    return bn / (bn + la)


def is_repetitive(text):
    words = re.findall(r"[\u0980-\u09FF]+", text)
    if len(words) < 200:
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


def validate_markdown(text):
    lines = text.splitlines()
    first = ""
    for line in lines:
        if line.strip():
            first = line.strip()
            break
    if not re.match(r"^# [^#]", first):
        return False, "article does not start with an H1 title"
    if len(re.findall(r"^## ", text, flags=re.M)) < 3:
        return False, "fewer than 3 H2 sections"
    if len(re.findall(r"^\s*[-*] ", text, flags=re.M)) < 3:
        return False, "fewer than 3 bullet points"
    if len(text) < 1500:
        return False, "article too short (" + str(len(text)) + " chars)"
    if FOREIGN_SCRIPT.search(text):
        return False, "foreign script characters found"
    if bengali_ratio(text) < 0.8:
        return False, "not enough Bengali text"
    bad, why = is_repetitive(text)
    if bad:
        return False, why
    return True, ""


def try_model(cfg, model, topic):
    last_reason = "unknown"
    for attempt in range(1, MAX_ATTEMPTS_PER_MODEL + 1):
        temp = 0.6
        if attempt > 1:
            temp = 0.45
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(topic)},
            ],
            "temperature": temp,
            "top_p": 0.9,
            "max_tokens": 3500,
        }
        try:
            r = requests.post(
                GROQ_CHAT_URL, headers=groq_headers(cfg), json=payload, timeout=LLM_TIMEOUT
            )
        except requests.exceptions.RequestException as e:
            last_reason = "network error: " + str(e)
            log("WARN", model + " attempt " + str(attempt) + ": " + last_reason)
            time.sleep(3)
            continue

        if r.status_code == 401:
            raise FatalError("GROQ_API_KEY was rejected by Groq (HTTP 401).")
        if r.status_code == 429:
            try:
                wait = float(r.headers.get("retry-after", 15))
            except ValueError:
                wait = 15.0
            wait = min(max(wait, 3.0), 60.0)
            last_reason = "rate limited (HTTP 429)"
            log("WARN", model + ": rate limited, waiting " + str(int(wait)) + "s")
            time.sleep(wait)
            continue
        if r.status_code >= 500:
            last_reason = "Groq server error " + str(r.status_code)
            log("WARN", model + " attempt " + str(attempt) + ": " + last_reason)
            time.sleep(5)
            continue
        if r.status_code != 200:
            return None, "HTTP " + str(r.status_code) + ": " + err_message(r)

        try:
            content = r.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError, ValueError):
            last_reason = "unexpected response format"
            log("WARN", model + " attempt " + str(attempt) + ": " + last_reason)
            continue

        markdown = clean_markdown(content)
        ok, why = validate_markdown(markdown)
        if ok:
            return markdown, ""
        last_reason = "quality check failed: " + why
        log("WARN", model + " attempt " + str(attempt) + ": " + last_reason)
    return None, last_reason


def generate_article(cfg, topic):
    models = discover_models(cfg)
    log("INFO", "Model candidates: " + ", ".join(models))
    last_reason = "no model was tried"
    for model in models:
        log("INFO", "Trying model: " + model)
        markdown, reason = try_model(cfg, model, topic)
        if markdown is not None:
            log("INFO", "Valid article generated with " + model)
            return markdown, model
        last_reason = model + " -> " + reason
        log("WARN", "Skipping " + model + ": " + reason[:300])
    raise FatalError("All models failed. Last reason: " + last_reason)


# ----------------------------------------------------------------------
# GitHub: commit file
# ----------------------------------------------------------------------
def gh_headers(cfg):
    return {
        "Authorization": "Bearer " + cfg["GH_TOKEN"],
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "daily-bengali-blog-bot",
    }


def gh_contents_url(cfg, path):
    return (
        GITHUB_API + "/repos/" + cfg["GH_OWNER"] + "/" + cfg["GH_REPO"]
        + "/contents/" + quote(path)
    )


def gh_existing_sha(cfg, path):
    params = {}
    if cfg["GH_BRANCH"]:
        params["ref"] = cfg["GH_BRANCH"]
    r = requests.get(
        gh_contents_url(cfg, path), headers=gh_headers(cfg), params=params, timeout=HTTP_TIMEOUT
    )
    if r.status_code == 200:
        data = r.json()
        if isinstance(data, dict):
            return data.get("sha")
        return None
    if r.status_code == 404:
        return None
    raise FatalError(
        "GitHub read error for " + path + ": HTTP " + str(r.status_code) + " " + err_message(r)
    )


def commit_file(cfg, path, text, message):
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    for attempt in range(1, 4):
        payload = {"message": message, "content": encoded}
        if cfg["GH_BRANCH"]:
            payload["branch"] = cfg["GH_BRANCH"]
        sha = gh_existing_sha(cfg, path)
        if sha:
            payload["sha"] = sha
        try:
            r = requests.put(
                gh_contents_url(cfg, path), headers=gh_headers(cfg), json=payload, timeout=HTTP_TIMEOUT
            )
        except requests.exceptions.RequestException as e:
            log("WARN", "GitHub network error (attempt " + str(attempt) + "): " + str(e))
            time.sleep(3)
            continue

        if r.status_code in (200, 201):
            html_url = ""
            try:
                html_url = r.json().get("content", {}).get("html_url", "")
            except ValueError:
                pass
            return html_url
        if r.status_code == 401:
            raise FatalError("GH_TOKEN is invalid or expired (HTTP 401).")
        if r.status_code == 403:
            raise FatalError(
                "GitHub refused the write (HTTP 403): " + err_message(r)
                + " | The token needs write access to repository contents."
            )
        if r.status_code == 404:
            raise FatalError(
                "Repository or branch not found (HTTP 404). Check GH_OWNER, GH_REPO, GH_BRANCH "
                "and that the token can access this repo."
            )
        if r.status_code in (409, 422) and attempt < 3:
            log("WARN", "Conflict while committing (HTTP " + str(r.status_code) + "), retrying...")
            time.sleep(2)
            continue
        raise FatalError(
            "GitHub commit failed: HTTP " + str(r.status_code) + " " + err_message(r)
        )
    raise FatalError("GitHub commit failed after 3 attempts.")


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main():
    cfg = load_config()

    topic = cfg["BLOG_TOPIC"]
    if not topic:
        topic = random.choice(TOPICS)
    log("INFO", "Topic: " + topic)

    markdown, model = generate_article(cfg, topic)

    filename = "posts/" + now_dhaka().strftime("%Y%m%d_%H%M%S") + ".md"
    log("INFO", "Committing " + filename + " to " + cfg["GH_OWNER"] + "/" + cfg["GH_REPO"])
    url = commit_file(cfg, filename, markdown + "\n", "Add daily post: " + filename)

    log("INFO", "Done. Model used: " + model)
    if url:
        log("INFO", "File URL: " + url)


if __name__ == "__main__":
    try:
        main()
    except ConfigError as e:
        log("ERROR", "Configuration problem: " + str(e))
        sys.exit(1)
    except FatalError as e:
        log("ERROR", str(e))
        sys.exit(1)
    except Exception as e:
        log("ERROR", "Unexpected " + type(e).__name__ + ": " + str(e))
        sys.exit(1)
