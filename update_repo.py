import os
import json
import urllib.request
import urllib.error
import re

def parse_version_tuple(v_str):
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
    print("[*] Execution target mode: all (dynamic sources scan)")

    headers = {"User-Agent": "Nuvio-Repo-Sync-Bot/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    sources_path = "sources.json"
    if not os.path.exists(sources_path):
        print(f"[!] Error: {sources_path} not found in repository root.")
        exit(1)

    try:
        with open(sources_path, "r", encoding="utf-8") as f:
            targets = json.load(f)
    except json.JSONDecodeError as e:
        print(f"[!] Error parsing {sources_path}: {e}")
        exit(1)

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

    bundled_targets = {}
    for target in targets:
        b_id = target["bundle_id"]
        bundled_targets.setdefault(b_id, []).append(target)

    for bundle_id, group in bundled_targets.items():
        highest_version_data = None
        highest_version_tuple = (-1,)
        best_target = group[0]

        print(f"[*] Evaluating sources for bundle identifier: {bundle_id}")

        for target in group:
            print(f" -> Checking upstream: {target['default_name']} ({target.get('developerName')}) [{target['api']}]")
            req = urllib.request.Request(target["api"], headers=headers)
            
            try:
                with urllib.request.urlopen(req) as response:
                    data = json.loads(response.read().decode())
            except urllib.error.HTTPError as e:
                print(f"    [!] HTTP error: {e.code} - {e.reason}")
                continue
            except Exception as e:
                print(f"    [!] Failed to fetch release: {e}")
                continue

            tag = data.get("tag_name")
            if not tag:
                print(f"    [!] No tag_name found.")
                continue

            version = clean_version(tag)
            v_tuple = parse_version_tuple(version)

            selected_asset = None
            for asset in data.get("assets", []):
                name = asset.get("name", "").lower()
                if name.endswith(".ipa"):
                    if bundle_id == "com.nuvio.enhanced" and "tvos" in name:
                        continue
                    selected_asset = asset
                    break
            
            if not selected_asset:
                for asset in data.get("assets", []):
                    if asset.get("name", "").endswith(".ipa"):
                        selected_asset = asset
                        break

            if not selected_asset:
                print(f"    [!] No valid .ipa asset found in latest release.")
                continue

            print(f"    [+] Found version {version} (Tuple: {v_tuple}) from {target.get('developerName')}")

            if v_tuple > highest_version_tuple:
                highest_version_tuple = v_tuple
                highest_version_data = data
                best_target = target

        if not highest_version_data:
            print(f"[!] No valid releases found across sources for {bundle_id}.")
            continue

        tag = highest_version_data.get("tag_name")
        version = clean_version(tag)
        pub_date = highest_version_data.get("published_at", "").split("T")[0]
        body = highest_version_data.get("body", "No release notes provided.")

        ipa_url = ""
        ipa_size = 50000000
        for asset in highest_version_data.get("assets", []):
            name = asset.get("name", "").lower()
            if name.endswith(".ipa"):
                if bundle_id == "com.nuvio.enhanced" and "tvos" in name:
                    continue
                ipa_url = asset.get("browser_download_url")
                ipa_size = asset.get("size", 50000000)
                break
        
        if not ipa_url:
            for asset in highest_version_data.get("assets", []):
                if asset.get("name", "").endswith(".ipa"):
                    ipa_url = asset.get("browser_download_url")
                    ipa_size = asset.get("size", 50000000)
                    break

        # Find or create app block in repo.json
        app = next((item for item in source_data["apps"] if item.get("bundleIdentifier") == bundle_id), None)

        if not app:
            app = {
                "bundleIdentifier": bundle_id,
                "versions": []
            }
            source_data["apps"].append(app)

        # Force update all main app details to match the WINNING source (e.g. luqmanfadlli)
        app["name"] = best_target["default_name"]
        app["developerName"] = best_target.get("developerName", "Unknown Team")
        app["subtitle"] = f"Official release ({best_target['default_name']})"
        app["localizedDescription"] = f"Automatically synced release for {best_target['default_name']} via {best_target.get('developerName')}."
        app["iconURL"] = best_target.get("iconURL", "")
        app["tintColor"] = "#FF5733"
        app["category"] = "entertainment"

        versions = app.setdefault("versions", [])
        existing_versions = [v.get("version") for v in versions]
        has_newer_or_equal = any(parse_version_tuple(v) >= parse_version_tuple(version) for v in existing_versions)

        if not has_newer_or_equal:
            new_version_entry = {
                "version": version,
                "date": pub_date,
                "localizedDescription": body[:200] + "..." if body else "No description.",
                "downloadURL": ipa_url,
                "size": ipa_size,
                "minOSVersion": "16.1"
            }
            versions.insert(0, new_version_entry)
            updated = True
            print(f"[+] Winner selected: {best_target['default_name']} by {best_target.get('developerName')} with version {version}.")
        else:
            print(f"[*] Bundle {bundle_id} is already up to date, but metadata aligned to winner: {best_target.get('developerName')}.")
            # Even if version exists, ensure developerName matches the current winner context if it changed
            if app.get("developerName") != best_target.get("developerName"):
                app["developerName"] = best_target.get("developerName")
                updated = True

    if updated:
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(source_data, f, indent=2)
            print("[+] repo.json updated and saved successfully.")
        except Exception as e:
            print(f"[!] Failed to write to {json_path}: {e}")
            exit(1)
    else:
        print("[*] No changes detected; repo.json remains unchanged.")

if __name__ == "__main__":
    main()
