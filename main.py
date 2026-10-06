import os
import random
import requests
from datetime import datetime
import pytz

# Secrets & Environment variables
GH_TOKEN = os.getenv("GH_TOKEN")
GH_OWNER = os.getenv("GH_OWNER")
GH_REPO = os.getenv("GH_REPO")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

DEFAULT_TOPICS = [
    "কৃত্রিম বুদ্ধিমত্তা ও ভবিষ্যৎ প্রযুক্তি",
    "প্রযুক্তির দুনিয়ায় সাম্প্রতিক উদ্ভাবন",
    "দৈনন্দিন জীবনে সাইবার নিরাপত্তা",
    "রোবোটিক্স ও অটোমেশন"
]

def ask_topic():
    try:
        t = input("Topic: ").strip()
    except EOFError:
        t = ""
    if t:
        return t
    return random.choice(DEFAULT_TOPICS)

def generate_article(topic):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    prompt = f"Write a comprehensive, engaging blog article in Bengali about '{topic}'. Use markdown formatting."
    data = {
        "model": "llama3-8b-8192",
        "messages": [{"role": "user", "content": prompt}]
    }
    res = requests.post(url, headers=headers, json=data)
    if res.status_code == 200:
        return res.json()["choices"][0]["message"]["content"]
    else
        raise Exception(f"Groq API Error: {res.text}")

def publish_to_github(topic, content):
    tz = pytz.timezone("Asia/Dhaka")
    now = datetime.now(tz)
    date_str = now.strftime("%Y-%m-%d")
    filename = f"posts/{now.strftime('%Y%m%d_%H%M%S')}.md"
    
    url = f"https://api.github.com/repos/{GH_OWNER}/{GH_REPO}/contents/{filename}"
    headers = {
        "Authorization": f"token {GH_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    import base64
    encoded_content = base64.b64encode(content.encode('utf-8')).decode('utf-8')
    
    data = {
        "message": f"Add post: {topic}",
        "content": encoded_content
    }
    res = requests.put(url, headers=headers, json=data)
    if res.status_code in [200, 201]:
        print("Successfully published to GitHub!")
    else:
        print(f"Failed to publish: {res.text}")

def main():
    topic = ask_topic()
    print(f"Generating content for topic: {topic}")
    content = generate_article(topic)
    publish_to_github(topic, content)

if __name__ == "__main__":
    mai
