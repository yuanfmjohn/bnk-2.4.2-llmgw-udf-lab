#!/usr/bin/env python3
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request


def api_request(api_base, method, path, payload=None, token=None):
    """Send a JSON request to the BNK controller API and return parsed JSON."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{api_base}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(req) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else {}


def get_admin_token(api_base):
    """Authenticate as admin using the f5-aigateway-admin Kubernetes Secret."""
    admin_pass = subprocess.check_output(
        ["kubectl", "get", "secret", "f5-aigateway-admin", "-n", "f5-bnk-instance", "-o", "jsonpath={.data.password}"],
        text=True,
    ).strip()
    admin_pass = base64.b64decode(admin_pass).decode("utf-8").strip()
    res = api_request(api_base, "POST", "/api/v1/auth/login", {"username": "admin", "password": admin_pass})
    return res["access_token"]


def ensure_governance_hierarchy(api_base, token, tenant="demo", org_id="demo-org", principal_id="demo-app"):
    """Ensure the org, team, and service principal exist (restoring tombstones if previously deleted)."""
    tombstones = api_request(api_base, "GET", f"/api/v1/tenants/{tenant}/governance-tombstones", token=token).get("items", [])
    org_tombstone = next((t for t in tombstones if t.get("kind") == "org" and t.get("id") == org_id), None)
    if org_tombstone:
        print(f"Restoring deleted org '{org_id}' from tombstone...")
        api_request(api_base, "POST", f"/api/v1/tenants/{tenant}/governance-tombstones/orgs/{org_id}/restore", {}, token=token)

    orgs = api_request(api_base, "GET", f"/api/v1/tenants/{tenant}/governance/orgs", token=token).get("items", [])
    if not any(o.get("id") == org_id for o in orgs):
        print(f"Creating org '{org_id}'...")
        api_request(api_base, "POST", f"/api/v1/tenants/{tenant}/governance/orgs", {"id": org_id, "displayName": org_id}, token=token)

    teams = api_request(api_base, "GET", f"/api/v1/tenants/{tenant}/governance/teams", token=token).get("items", [])
    team = next((t for t in teams if t.get("orgId") == org_id), None)
    if not team:
        print(f"Creating default team under org '{org_id}'...")
        res = api_request(
            api_base,
            "POST",
            f"/api/v1/tenants/{tenant}/governance/teams",
            {"orgId": org_id, "name": "default", "displayName": "Default"},
            token=token,
        )
        team_id = (res.get("data") or res).get("id")
    else:
        team_id = team["id"]

    principals = api_request(api_base, "GET", f"/api/v1/tenants/{tenant}/governance/principals", token=token).get("items", [])
    principal = next((p for p in principals if p.get("id") == principal_id), None)
    if not principal:
        print(f"Creating service principal '{principal_id}' in team '{team_id}'...")
        api_request(
            api_base,
            "POST",
            f"/api/v1/tenants/{tenant}/governance/principals",
            {"id": principal_id, "displayName": principal_id, "teamId": team_id, "kind": "service"},
            token=token,
        )
    elif not principal.get("active", True):
        print(f"Reactivating service principal '{principal_id}'...")
        api_request(api_base, "POST", f"/api/v1/tenants/{tenant}/governance/principals/{principal_id}/reactivate", {}, token=token)


def main():
    try:
        ep_ip = subprocess.check_output(
            ["kubectl", "get", "ep", "f5-cne-controller-api", "-n", "f5-bnk-instance", "-o", "jsonpath={.subsets[0].addresses[0].ip}"],
            text=True,
        ).strip()
    except Exception as e:
        print("Failed to get controller endpoint IP:", e, file=sys.stderr)
        sys.exit(1)

    api_base = f"http://{ep_ip}:8443"
    print(f"Connecting to controller API base: {api_base}")

    try:
        token = get_admin_token(api_base)
        print("Login successful!")
    except Exception as e:
        print("Failed to login to API:", e, file=sys.stderr)
        sys.exit(1)

    try:
        ensure_governance_hierarchy(api_base, token)
    except urllib.error.HTTPError as e:
        print(f"Governance setup failed: HTTP {e.code}: {e.read().decode()}", file=sys.stderr)
        sys.exit(1)

    # List virtual keys
    try:
        keys = api_request(api_base, "GET", "/api/v1/tenants/demo/virtual-keys", token=token)
        print("Existing virtual keys:")
        items = keys.get("items", []) if isinstance(keys, dict) else keys
        for k in items:
            print(f"  - ID: {k.get('id')} Name: {k.get('name')} Active: {k.get('active')} ModelScope: {k.get('modelScope')}")
    except urllib.error.HTTPError as e:
        print(f"List virtual keys failed: HTTP {e.code}: {e.read().decode()}", file=sys.stderr)

    # Create new virtual key scoped to Gemini (primary) and NGC (optional) models
    key_name = f"demo-key-{int(time.time())}"
    key_payload = {
        "name": key_name,
        "principalId": "demo-app",
        "modelScope": [
            "gemini-flash-latest",
            "gemini-3.5-flash-lite",
            "deepseek-ai/deepseek-v4.1-flash",
            "openai/gpt-oss-20b",
        ],
        "providerKeys": [],
    }
    try:
        res = api_request(api_base, "POST", "/api/v1/tenants/demo/virtual-keys", key_payload, token=token)
        vkey_token = None
        if "data" in res and isinstance(res["data"], dict) and "token" in res["data"]:
            vkey_token = res["data"]["token"]
        elif "token" in res:
            vkey_token = res["token"]
        elif "data" in res and isinstance(res["data"], str):
            vkey_token = res["data"]

        if vkey_token:
            out_path = os.path.expanduser("~/aigateway/tests/demo_vkey.txt")
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "w") as f:
                f.write(vkey_token.strip() + "\n")
            os.chmod(out_path, 0o600)
            print(f"Successfully created virtual key {key_name} (token saved to {out_path})")
        else:
            print("Unexpected virtual key response:", res, file=sys.stderr)
            sys.exit(1)
    except urllib.error.HTTPError as e:
        print(f"Failed to create virtual key: HTTP {e.code}: {e.read().decode()}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
