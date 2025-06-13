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
        "headers": {"Authorization": f"Bearer {os.getenv('TOGETHER_KEY').strip()}", "Content-Type": "application/json"},
        "payload": {"model": "mistralai/Mixtral-8x7B-Instruct-v0.1", "max_tokens": 1000}
    },
    {
        "name": "fireworks",
        "url": "https://api.fireworks.ai/inference/v1/chat/completions",
        "headers": {"Authorization": f"Bearer {os.getenv('FIREWORKS_KEY').strip()}", "Content-Type": "application/json"},
        "payload": {
            "model": "accounts/fireworks/models/llama-v3p1-8b-instruct",
            "max_tokens": 2000,
            "temperature": 0.7,
            "top_p": 1
        }
    },
    {
        "name": "mistral",
        "url": "https://api.mistral.ai/v1/chat/completions",
        "headers": {"Authorization": f"Bearer {os.getenv('MISTRAL_KEY').strip()}", "Content-Type": "application/json"},
        "payload": {"model": "mistral-small", "max_tokens": 1000}
    }
]

# Validate API keys and GH_TOKEN
for api in API_CONFIGS:
    if not os.getenv(api['name'].upper() + '_KEY'):
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

# Clear cache to avoid invalid fixes
if os.path.exists(CACHE_FILE):
    os.remove(CACHE_FILE)
FIX_CACHE = load_cache()

# ===== AI FIX FUNCTION =====
def ai_fix_code(issue):
    cache_key = f"{issue['title']}-{issue['body'][:50]}"
    if cache_key in FIX_CACHE:
        print(f"Using cached fix for issue #{issue['number']}")
        return FIX_CACHE[cache_key]

    prompt = f"""Generate a valid Git patch file to fix this GitHub issue. The patch MUST:
    - Be in unified diff format.
    - Include correct file paths relative to the repository root.
    - Contain only the diff content (no explanations or extra text).
    - Apply cleanly to the repository's current state.

    Issue Title: {issue['title']}
    Issue Body: {issue['body']}

    Example patch:
    ```diff
    --- a/README.md
    +++ b/README.md
    @@ -1,1 +1,1 @@
    -Helllo World
    +Hello World
    ```

    Return ONLY the patch content:
    """

    for api in API_CONFIGS:
        try:
            print(f"Trying {api['name']} API for issue #{issue['number']}...")
            response = requests.post(
                api["url"],
                headers=api["headers"],
                json={
                    **api["payload"],
                    "messages": [{"role": "user", "content": prompt}]
                },
                timeout=20
            )
            response.raise_for_status()
            print(f"{api['name']} response: {response.status_code}")
            content = response.json()["choices"][0]["message"]["content"].strip()
            
            # Log patch content for debugging
            print(f"Patch from {api['name']} for issue #{issue['number']}:\n{content[:500]}...")
            
            # Validate patch format
            if "--- a/" in content and "+++ b/" in content and "@@" in content:
                FIX_CACHE[cache_key] = content
                save_cache(FIX_CACHE)
                return content
            print(f"⚠️ Invalid patch format from {api['name']} for issue #{issue['number']}")
        except RequestException as e:
            print(f"⚠️ {api['name']} API error for issue #{issue['number']}: {str(e)[:200]}")
            time.sleep(2)

    print(f"⚠️ No valid fix generated for issue #{issue['number']}")
    return None

# ===== GITHUB AUTOMATION =====
def submit_fix(issue, fix):
    repo_url = issue["repository_url"].replace("https://api.github.com/repos/", "")
    local_dir = f"./temp_repo_{issue['id']}"
    branch_name = f"fix-issue-{issue['number']}"

    try:
        if os.path.exists(local_dir):
            shutil.rmtree(local_dir)

        print(f"Cloning repo: https://github.com/{repo_url}.git for issue #{issue['number']}")
        repo = Repo.clone_from(f"https://x-access-token:{GH_TOKEN}@github.com/{repo_url}.git", local_dir)
        repo.git.checkout("-b", branch_name)

        with open(f"{local_dir}/fix.patch", "w") as f:
            f.write(fix)

        print(f"Checking patch for issue #{issue['number']}")
        try:
            repo.git.execute(["git", "apply", "--check", "fix.patch"])
        except:
            print(f"⚠️ Invalid patch for issue #{issue['number']} during git apply")
            return None

        repo.git.execute(["git", "apply", "fix.patch"])
        repo.git.add(A=True)
        repo.git.commit(m=f"Fix: {issue['title']} (Issue #{issue['number']})")
        print(f"Pushing branch {branch_name} for issue #{issue['number']}")
        repo.git.push("origin", branch_name)

        headers = {"Authorization": f"token {GH_TOKEN}"}
        pr_data = {
            "title": f"Fix: {issue['title']}",
            "head": branch_name,
            "base": "main",
            "body": f"Automated fix for issue #{issue['number']}\n\n{fix[:500]}..."
        }
        print(f"Creating pull request for {repo_url}")
        response = requests.post(
            f"https://api.github.com/repos/{repo_url}/pulls",
            headers=headers,
            json=pr_data
        )
        response.raise_for_status()
        return response.json()["html_url"]

    except Exception as e:
        print(f"⚠️ Failed to submit fix for issue #{issue['number']}: {str(e)[:200]}")
        return None
    finally:
        if os.path.exists(local_dir):
            shutil.rmtree(local_dir)

# ===== MAIN EXECUTION =====
if __name__ == "__main__":
    headers = {"Authorization": f"token {GH_TOKEN}"}
    try:
        print("Fetching issues from GitHub")
        response = requests.get(
            "https://api.github.com/search/issues?q=label:good-first-issue+state:open",
            headers=headers,
            timeout=30
        )
        response.raise_for_status()
        issues = response.json()["items"][:3]
        print(f"Found {len(issues)} issues: {[issue['number'] for issue in issues]}")

        for issue in issues:
            print(f"Processing issue #{issue['number']}: {issue['title']}")
            fix = ai_fix_code(issue)
            if not fix:
                print(f"⚠️ Skipping issue #{issue['number']} due to no valid fix")
                continue

            pr_link = submit_fix(issue, fix)
            if pr_link:
                print(f"✅ Fix submitted for issue #{issue['number']}: {pr_link}")
            else:
                print(f"⚠️ Failed to submit fix for issue #{issue['number']}")

    except RequestException as e:
        print(f"⚠️ Failed to fetch issues: {str(e)[:200]}")
