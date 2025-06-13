import os
import requests
import json
from git import Repo
import shutil
import time
from requests.exceptions import RequestException

# ===== API CONFIGURATION =====
API_CONFIGS = [
    {
        "name": "together",
        "url": "https://api.together.xyz/v1/chat/completions",
        "key": os.getenv("TOGETHER_KEY"),
        "payload": {"model": "mistralai/Mixtral-8x7B-Instruct-v0.1"}
    },
    {
        "name": "fireworks",
        "url": "https://api.fireworks.ai/inference/v1/chat/completions",
        "key": os.getenv("FIREWORKS_KEY"),
        "payload": {"model": "accounts/fireworks/models/codellama-34b"}
    },
    {
        "name": "mistral",
        "url": "https://api.mistral.ai/v1/chat/completions",
        "key": os.getenv("MISTRAL_KEY"),
        "payload": {"model": "mistral-small"}
    }
]

# Validate API keys
for api in API_CONFIGS:
    if not api["key"]:
        raise ValueError(f"Missing API key for {api['name']}")

GH_TOKEN = os.getenv("GH_TOKEN")
if not GH_TOKEN:
    raise ValueError("Missing GH_TOKEN environment variable")

# ===== CACHING =====
CACHE_FILE = "fix_cache.json"
def load_cache():
    try:
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_cache(cache):
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        print(f"⚠️ Failed to save cache: {e}")

FIX_CACHE = load_cache()

# ===== AI FIX FUNCTION =====
def ai_fix_code(issue):
    cache_key = f"{issue['title']}-{issue['body'][:50]}"
    if cache_key in FIX_CACHE:
        return FIX_CACHE[cache_key]

    prompt = f"""
    Fix this GitHub issue (return ONLY a valid git patch):
    Title: {issue['title']}
    Description: {issue['body']}
    """

    for api in API_CONFIGS:
        try:
            headers = {"Authorization": f"Bearer {api['key']}"}
            response = requests.post(
                api["url"],
                headers=headers,
                json={**api["payload"], "messages": [{"role": "user", "content": prompt}]},
                timeout=30
            )
            response.raise_for_status()
            fix = response.json()["choices"][0]["message"]["content"]
            
            # Basic validation of fix
            if not fix.strip() or len(fix) > 10000:
                print(f"⚠️ Invalid fix from {api['name']}: Empty or too large")
                continue
                
            FIX_CACHE[cache_key] = fix
            save_cache(FIX_CACHE)
            return fix
        except RequestException as e:
            print(f"⚠️ {api['name']} failed: {e}")
            time.sleep(2)

    return "Failed to generate fix."

# ===== GITHUB AUTOMATION =====
def submit_fix(issue, fix):
    repo_url = issue["repository_url"].replace("https://api.github.com/repos/", "")
    local_dir = f"./temp_repo_{issue['id']}"
    branch_name = f"fix-issue-{issue['number']}"

    try:
        if os.path.exists(local_dir):
            shutil.rmtree(local_dir)

        repo = Repo.clone_from(f"https://x-access-token:{GH_TOKEN}@github.com/{repo_url}.git", local_dir)
        repo.git.checkout("-b", branch_name)

        with open(f"{local_dir}/fix.patch", "w") as f:
            f.write(fix)

        try:
            repo.git.execute(["git", "apply", "--check", "fix.patch"])
        except:
            print(f"⚠️ Invalid patch for issue {issue['number']}")
            return None

        repo.git.execute(["git", "apply", "fix.patch"])
        repo.git.add(A=True)
        repo.git.commit(m=f"Fix: {issue['title']} (Issue #{issue['number']})")
        repo.git.push("origin", branch_name)

        headers = {"Authorization": f"token {GH_TOKEN}"}
        pr_data = {
            "title": f"Fix: {issue['title']}",
            "head": branch_name,
            "base": "main",
            "body": f"Automated fix for issue #{issue['number']}\n\n{fix[:500]}..."
        }
        response = requests.post(
            f"https://api.github.com/repos/{repo_url}/pulls",
            headers=headers,
            json=pr_data
        )
        response.raise_for_status()
        return response.json()["html_url"]

    except Exception as e:
        print(f"⚠️ Failed to submit fix for issue {issue['number']}: {e}")
        return None
    finally:
        if os.path.exists(local_dir):
            shutil.rmtree(local_dir)

# ===== MAIN EXECUTION =====
if __name__ == "__main__":
    headers = {"Authorization": f"token {GH_TOKEN}"}
    try:
        response = requests.get(
            "https://api.github.com/search/issues?q=label:good-first-issue+state:open",
            headers=headers,
            timeout=30
        )
        response.raise_for_status()
        issues = response.json()["items"][:3]

        for issue in issues:
            fix = ai_fix_code(issue)
            if fix.startswith("Failed"):
                print(f"⚠️ No fix generated for issue {issue['number']}")
                continue

            pr_link = submit_fix(issue, fix)
            if pr_link:
                print(f"✅ Fix submitted for issue #{issue['number']}: {pr_link}")
            else:
                print(f"⚠️ Failed to submit fix for issue #{issue['number']}")

    except RequestException as e:
        print(f"⚠️ Failed to fetch issues: {e}")
