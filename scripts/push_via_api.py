"""
Push local commit to GitHub via Git Data API.
Workaround for: this sandbox can reach api.github.com but not github.com (port 443).
Uses fine-grained PAT with Contents: Read/Write on the target repo.
"""
import base64
import json
import os
import subprocess
import sys
import urllib.request
import urllib.error

TOK = os.environ.get("GITHUB_TOKEN")
if not TOK:
    print("ERROR: set GITHUB_TOKEN env var", file=sys.stderr)
    sys.exit(1)

OWNER = "Mrtangzx"
REPO = "agent-swarm-ideation"
API = f"https://api.github.com/repos/{OWNER}/{REPO}"


def call(method, path, data=None):
    url = API + path
    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {TOK}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if body:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def run(cmd, **kwargs):
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


# 1. Get list of files in local commit
sha = run(["git", "rev-parse", "HEAD"], cwd=".").stdout.strip()
print(f"local commit: {sha}")
files = run(["git", "ls-tree", "-r", "--name-only", "HEAD"], cwd=".").stdout.strip().splitlines()
print(f"files: {files}")

# 2. Create blobs
blobs = []
for path in files:
    content = run(["git", "cat-file", "blob", f"HEAD:{path}"], cwd=".").stdout
    # On Windows, subprocess returns str (decoded). On Linux, bytes. Handle both.
    if isinstance(content, bytes):
        try:
            text = content.decode("utf-8")
            payload = {"content": text, "encoding": "utf-8"}
        except UnicodeDecodeError:
            payload = {"content": base64.b64encode(content).decode("ascii"), "encoding": "base64"}
    else:
        # already str on Windows
        payload = {"content": content, "encoding": "utf-8"}
    code, body = call("POST", "/git/blobs", payload)
    if code != 201:
        print(f"  blob FAIL {path}: {code} {body}", file=sys.stderr)
        sys.exit(1)
    blobs.append({"path": path, "sha": body["sha"]})
    print(f"  blob OK {path} -> {body['sha'][:7]}")

# 3. Create tree
tree_payload = {"tree": [{"path": b["path"], "mode": "100644", "type": "blob", "sha": b["sha"]} for b in blobs]}
code, tree = call("POST", "/git/trees", tree_payload)
if code != 201:
    print(f"  tree FAIL: {code} {tree}", file=sys.stderr)
    sys.exit(1)
print(f"tree OK -> {tree['sha'][:7]}")

# 4. Get author info from local commit
author_info = run(["git", "log", "-1", "--format=%an|%ae|%at"], cwd=".").stdout.strip()
name, email, ts = author_info.split("|")
committer = {"name": name, "email": email, "date": run(["git", "log", "-1", "--format=%cI"], cwd=".").stdout.strip()}

# 5. Create commit (no parents = initial commit)
commit_payload = {
    "message": run(["git", "log", "-1", "--format=%B"], cwd=".").stdout.strip(),
    "tree": tree["sha"],
    "parents": [],
    "author": committer,
    "committer": committer,
}
code, commit = call("POST", "/git/commits", commit_payload)
if code != 201:
    print(f"  commit FAIL: {code} {commit}", file=sys.stderr)
    sys.exit(1)
print(f"commit OK -> {commit['sha'][:7]}")

# 6. Update main ref
ref_payload = {"sha": commit["sha"]}
code, ref = call("POST", "/git/refs", ref_payload)
# POST to /git/refs creates under a sub-namespace; we want /git/refs/heads/main specifically
# Retry with correct path
if code != 201:
    # try direct
    code, ref = call("PATCH", "/git/refs/heads/main", ref_payload)
if code not in (200, 201):
    print(f"  ref FAIL: {code} {ref}", file=sys.stderr)
    sys.exit(1)
print(f"ref updated -> main = {commit['sha'][:7]}")

print(f"\nDONE. https://github.com/{OWNER}/{REPO}")
