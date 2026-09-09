import os
import json
import urllib.request
import urllib.error
import re

def parse_version_tuple(v_str):
    # Extracts all numbers from a version string to compare them logically (e.g., "0.4.14" -> (0, 4, 14))
    numbers = re.findall(r'\d+', v_str)
    return tuple(map(int, numbers)) if numbers else (0, 0, 0)

def clean_version(tag):
    if not tag:
        return "1.0.0"
    
    match = re.search(r'(\d+\.\d+\.\d+)', tag)
    if match:
        base_ver = match.group(1)
        build_match = re.search(r'(?:build|-)(\d+)', tag, re.IGNORECASE)
        if build_match:
            return f"{base_ver} (build {build_match.group(1)})"
        return base_ver
        
    return re.sub(r'^[a-zA-Z_-]+v?', '', tag)

def main():
    token = os.environ.get("GH_TOKEN")
    webhook_variant = os.environ.get("WEBHOOK_VARIANT")
    manual_variant = os.environ.get("MANUAL_VARIANT")

    target_choice = webhook_variant if webhook_variant else (manual_variant if manual_variant else "all")
    print(f"[*] Execution target mode: {target_choice}")

    headers = {"User-Agent": "Nuvio-Repo-Sync-Bot/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    sources_path = "sources.json"
    if not os.path.exists(sources_path):
        print(f"[!] Error: {sources_path} not found in repository root.")
        exit(1)

    try:
        with open(sources_path, "r", encoding="utf-8") as f:
            all_targets = json.load(f)
    except json.JSONDecodeError as e:
        print(f"[!] Error parsing {sources_path}: {e}")
        exit(1)

    valid_keys = [t["key"] for t in all_targets]
    targets = [t for t in all_targets if t["key"] == target_choice] if target_choice in valid_keys else all_targets

    json_path = "repo.json"
    if not os.path.exists(json_path):
        print(f"[!] Error: {json_path} not found in repository root.")
        exit(1)

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            source_data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"[!] Error parsing {json_path}: {e}")
        exit(1)

    if "apps" not in source_data:
        source_data["apps"] = []

    updated = False

    for target in targets:
        print(f"[*] Fetching latest release info for {target['default_name']} ({target.get('developerName')})...")
        req = urllib.request.Request(target["api"], headers=headers)
        
        try:
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())
        except urllib.error.HTTPError as e:
            print(f"[!] HTTP error fetching {target['default_name']}: {e.code} - {e.reason}")
            continue
        except Exception as e:
            print(f"[!] Failed to fetch release for {target['default_name']}: {e}")
            continue

        tag = data.get("tag_name")
        if not tag:
            print(f"[!] No tag_name found for {target['default_name']}.")
            continue

        version = clean_version(tag)
        pub_date = data.get("published_at", "").split("T")[0]
        body = data.get("body", "No release notes provided.")

        ipa_url = ""
        ipa_size = 0
        for asset in data.get("assets", []):
            asset_name = asset.get("name", "")
            if asset_name.endswith(".ipa"):
                ipa_url = asset.get("browser_download_url")
                ipa_size = asset.get("size", 50000000)
                break

        if not ipa_url:
            print(f"[!] No .ipa asset found in the latest release for {target['default_name']}.")
            continue

        target_bundle_id = target["bundle_id"]
        icon_url = target.get("iconURL", "")

        app = next((item for item in source_data["apps"] if item.get("bundleIdentifier") == target_bundle_id), None)

        if not app:
            app = {
                "name": target["default_name"],
                "bundleIdentifier": target_bundle_id,
                "developerName": target.get("developerName", "Unknown Team"),
                "subtitle": f"Official release ({target['default_name']})",
                "localizedDescription": f"Automatically synced release for {target['default_name']}.",
                "iconURL": icon_url,
                "versions": []
            }
            source_data["apps"].append(app)

        app["name"] = target["default_name"]
        app["bundleIdentifier"] = target_bundle_id
        if icon_url:
            app["iconURL"] = icon_url

        versions = app.setdefault("versions", [])
        existing_versions = [v.get("version") for v in versions]

        # Check if this specific version string already exists
        version_exists = version in existing_versions
        
        # Also check if any existing version is newer/equal logically
        has_newer_or_equal = any(parse_version_tuple(v) >= parse_version_tuple(version) for v in existing_versions)

        if not version_exists and not has_newer_or_equal:
            new_version_entry = {
                "version": version,
                "date": pub_date,
                "localizedDescription": body[:200] + "..." if body else "No description.",
                "downloadURL": ipa_url,
                "size": ipa_size,
                "minOSVersion": "15.0"
            }
            versions.insert(0, new_version_entry)
            updated = True
            print(f"[+] Successfully added newer version {version} for {target['default_name']}")
        else:
            print(f"[*] {target['default_name']} version {version} is already present or older than current feed.")

    if updated:
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(source_data, f, indent=2)
            print("[+] repo.json updated and saved successfully.")
        except Exception as e:
            print(f"[!] Failed to write to {json_path}: {e}")
            exit(1)
    else:
        print("[*] No new versions detected; repo.json remains unchanged.")

if __name__ == "__main__":
    main()
