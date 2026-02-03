import json

data = json.load(open(r'C:\Users\onlin\.openclaw\workspace\chatgpt-export\conversations.json', encoding='utf-8'))

# Find categories
categories = {}
keywords = {
    'career': ['job', 'interview', 'resume', 'career', 'netflix', 'microsoft', 'salary'],
    'health': ['weight', 'fitness', 'workout', 'diet', 'health', 'sleep'],
    'mindfulness': ['present', 'awareness', 'meditation', 'consciousness', 'bliss'],
    'technical': ['code', 'python', 'api', 'system', 'distributed', 'kubernetes'],
    'academic': ['paper', 'ieee', 'journal', 'icgis', 'research', 'conference'],
    'ai': ['ai', 'agent', 'gpt', 'llm', 'machine learning', 'safety'],
    'finance': ['invest', 'money', 'rent', 'buy', 'financial', 'stock'],
}

for c in data:
    title = (c.get('title') or '').lower()
    for cat, words in keywords.items():
        if any(w in title for w in words):
            categories.setdefault(cat, []).append(c.get('title', 'Untitled'))

print("=== CONVERSATION CATEGORIES ===\n")
for cat, titles in sorted(categories.items(), key=lambda x: -len(x[1])):
    print(f"{cat.upper()} ({len(titles)} conversations)")

print("\n=== USER PROFILE FROM EXPORT ===\n")
user_data = json.load(open(r'C:\Users\onlin\.openclaw\workspace\chatgpt-export\user.json', encoding='utf-8'))
for k, v in user_data.items():
    if v and k not in ['id']:
        print(f"{k}: {v}")
