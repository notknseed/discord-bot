import json
import threading
import time
import os
import random
import re
import requests
from dotenv import load_dotenv
from datetime import datetime
from colorama import init, Fore, Style

init(autoreset=True)
load_dotenv()

discord_tokens_env = os.getenv('DISCORD_TOKENS', '')
if discord_tokens_env:
    discord_tokens = [token.strip() for token in discord_tokens_env.split(',') if token.strip()]
else:
    discord_token = os.getenv('DISCORD_TOKEN')
    if not discord_token:
        raise ValueError("Tidak ada Discord token yang ditemukan! Harap atur DISCORD_TOKENS atau DISCORD_TOKEN di .env.")
    discord_tokens = [discord_token]

google_api_keys = os.getenv('GOOGLE_API_KEYS', '').split(',')
google_api_keys = [key.strip() for key in google_api_keys if key.strip()]
if not google_api_keys:
    raise ValueError("Tidak ada Google API Key yang ditemukan! Harap atur GOOGLE_API_KEYS di .env.")

processed_message_ids = set()
used_api_keys = set()
last_generated_text = None
cooldown_time = 86400

# Conversation memory settings
user_conversations = {}  # To store conversation history by user ID
conversation_expiry = 3600  # Conversation expires after 1 hour of inactivity
max_conversation_length = 7  # Maximum number of previous exchanges to remember

def log_message(message, level="INFO"):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    if level.upper() == "SUCCESS":
        color, icon = Fore.GREEN, "✅"
    elif level.upper() == "ERROR":
        color, icon = Fore.RED, "🚨"
    elif level.upper() == "WARNING":
        color, icon = Fore.YELLOW, "⚠️"
    elif level.upper() == "WAIT":
        color, icon = Fore.CYAN, "⌛"
    else:
        color, icon = Fore.WHITE, "ℹ️"

    border = f"{Fore.MAGENTA}{'=' * 80}{Style.RESET_ALL}"
    formatted_message = f"{color}[{timestamp}] {icon} {message}{Style.RESET_ALL}"
    print(border)
    print(formatted_message)
    print(border)

def update_conversation_history(user_id, channel_id, user_message, bot_response):
    # Create a unique key for each user in each channel
    conversation_key = f"{user_id}:{channel_id}"
    
    # Get current timestamp
    current_time = time.time()
    
    # Create or update conversation entry
    if conversation_key not in user_conversations:
        user_conversations[conversation_key] = {
            "exchanges": [],
            "last_update": current_time
        }
    
    # Add the new exchange
    user_conversations[conversation_key]["exchanges"].append({
        "user": user_message,
        "bot": bot_response,
        "timestamp": current_time
    })
    
    # Update the timestamp
    user_conversations[conversation_key]["last_update"] = current_time
    
    # Trim conversation if it exceeds the maximum length
    if len(user_conversations[conversation_key]["exchanges"]) > max_conversation_length:
        user_conversations[conversation_key]["exchanges"] = user_conversations[conversation_key]["exchanges"][-max_conversation_length:]
    
    log_message(f"Updated conversation history for user {user_id} in channel {channel_id}. Total exchanges: {len(user_conversations[conversation_key]['exchanges'])}", "INFO")

def get_conversation_history(user_id, channel_id):
    conversation_key = f"{user_id}:{channel_id}"
    
    # If no conversation exists or it's expired, return empty
    if conversation_key not in user_conversations:
        return []
    
    # Check if conversation has expired
    current_time = time.time()
    if current_time - user_conversations[conversation_key]["last_update"] > conversation_expiry:
        # Conversation expired, remove it
        del user_conversations[conversation_key]
        log_message(f"Expired conversation removed for user {user_id} in channel {channel_id}", "INFO")
        return []
    
    return user_conversations[conversation_key]["exchanges"]

def cleanup_expired_conversations():
    current_time = time.time()
    keys_to_remove = []
    
    for key, conversation in user_conversations.items():
        if current_time - conversation["last_update"] > conversation_expiry:
            keys_to_remove.append(key)
    
    for key in keys_to_remove:
        del user_conversations[key]
    
    if keys_to_remove:
        log_message(f"Cleaned up {len(keys_to_remove)} expired conversations", "INFO")

def get_random_api_key():
    available_keys = [key for key in google_api_keys if key not in used_api_keys]
    if not available_keys:
        log_message("Semua API key terkena error 429. Menunggu 24 jam sebelum mencoba lagi...", "ERROR")
        time.sleep(cooldown_time)
        used_api_keys.clear()
        return get_random_api_key()
    return random.choice(available_keys)

def get_random_message_from_file():
    try:
        with open("pesan.txt", "r", encoding="utf-8") as file:
            messages = [line.strip() for line in file.readlines() if line.strip()]
            return random.choice(messages) if messages else "Tidak ada pesan tersedia di file."
    except FileNotFoundError:
        return "File pesan.txt tidak ditemukan!"

def generate_language_specific_prompt(user_message, prompt_language, persona=None, conversation_history=None):
    persona_prefix = ""
    if persona:
        persona_prefix = f"You are {persona}. Remember this character, but you show this character only if being asked. "
    
    history_text = ""
    if conversation_history and len(conversation_history) > 0:
        history_text = "Here's our conversation history (most recent last):\n"
        for exchange in conversation_history:
            if prompt_language == 'id':
                history_text += f"User: {exchange['user']}\nYou: {exchange['bot']}\n"
            else:
                history_text += f"User: {exchange['user']}\nYou: {exchange['bot']}\n"
        history_text += "\nRemember this context when replying. Keep your response conversational and natural.\n"
    
    if prompt_language == 'id':
        return f"{persona_prefix}{history_text}Balas pesan berikut dalam bahasa Indonesia, dengan mempertahankan konteks percakapan sebelumnya: {user_message}"
    elif prompt_language == 'en':
        return f"{persona_prefix}{history_text}Reply to the following message in English, maintaining the context of our previous conversation: {user_message}"
    else:
        log_message(f"Bahasa prompt '{prompt_language}' tidak valid. Pesan dilewati.", "WARNING")
        return None

def is_time_question(message):
    # List of patterns that indicate the user is asking for the time
    time_patterns = [
        "what time", "what's the time", "what is the time", 
        "current time", "time now", "time is it",
        "got the time", "tell me the time",
        "jam berapa", "sekarang jam", "waktu sekarang"
    ]
    return any(pattern in message.lower() for pattern in time_patterns)

def generate_random_time_response(prompt_language, persona=None):
    # Generate a random hour and minute
    random_hour = random.randint(1, 12)
    random_minute = random.randint(0, 59)
    am_pm = random.choice(["AM", "PM"])
    random_time = f"{random_hour}:{random_minute:02d} {am_pm}"
    
    if persona:
        # If there's a persona, send the time info to the AI to format it in character
        google_api_key = get_random_api_key()
        
        if prompt_language == 'id':
            special_prompt = f"You are {persona}. Seseorang bertanya jam berapa sekarang. Katakan bahwa sekarang jam {random_time}. Jawab dengan gaya khas karaktermu dengan 1 kalimat."
        else:
            special_prompt = f"You are {persona}. Someone asked what time it is. Tell them it's {random_time} now. Answer in your character's style with 1 sentence."
            
        url = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={google_api_key}'
        headers = {'Content-Type': 'application/json'}
        data = {'contents': [{'parts': [{'text': special_prompt}]}]}
        
        try:
            response = requests.post(url, headers=headers, json=data)
            response.raise_for_status()
            result = response.json()
            return result['candidates'][0]['content']['parts'][0]['text']
        except requests.exceptions.RequestException as e:
            log_message(f"Error generating time response: {e}", "ERROR")
    
    # Fallback if persona processing fails or if no persona is specified
    if prompt_language == 'id':
        return f"Sekarang jam {random_time}."
    else:
        return f"It's {random_time}."

def generate_reply(prompt, prompt_language, use_google_ai=True, persona=None, conversation_history=None):
    global last_generated_text
    
    # Check if it's a time-related question
    if use_google_ai and is_time_question(prompt):
        log_message(f"Detected time question: \"{prompt}\". Generating random time response.", "INFO")
        return generate_random_time_response(prompt_language, persona)
    
    if use_google_ai:
        google_api_key = get_random_api_key()
        lang_prompt = generate_language_specific_prompt(prompt, prompt_language, persona, conversation_history)
        if lang_prompt is None:
            return None
        ai_prompt = f"{lang_prompt}\n\nBuatlah menjadi 1 kalimat menggunakan bahasa kasual chatting di discord tanpa huruf kapital dan jangan selalu pakai emoticon"
        url = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={google_api_key}'
        headers = {'Content-Type': 'application/json'}
        data = {'contents': [{'parts': [{'text': ai_prompt}]}]}
        while True:
            try:
                response = requests.post(url, headers=headers, json=data)
                if response.status_code == 429:
                    log_message(f"API key {google_api_key} terkena rate limit (429). Menggunakan API key lain...", "WARNING")
                    used_api_keys.add(google_api_key)
                    return generate_reply(prompt, prompt_language, use_google_ai, persona, conversation_history)
                response.raise_for_status()
                result = response.json()
                generated_text = result['candidates'][0]['content']['parts'][0]['text']
                if generated_text == last_generated_text:
                    log_message("AI menghasilkan teks yang sama, meminta teks baru...", "WAIT")
                    continue
                last_generated_text = generated_text
                return generated_text
            except requests.exceptions.RequestException as e:
                log_message(f"Request failed: {e}", "ERROR")
                time.sleep(2)
    else:
        return get_random_message_from_file()

def get_channel_info(channel_id, token):
    headers = {'Authorization': token}
    channel_url = f"https://discord.com/api/v9/channels/{channel_id}"
    try:
        channel_response = requests.get(channel_url, headers=headers)
        channel_response.raise_for_status()
        channel_data = channel_response.json()
        channel_name = channel_data.get('name', 'Unknown Channel')
        guild_id = channel_data.get('guild_id')
        server_name = "Direct Message"
        if guild_id:
            guild_url = f"https://discord.com/api/v9/guilds/{guild_id}"
            guild_response = requests.get(guild_url, headers=headers)
            guild_response.raise_for_status()
            guild_data = guild_response.json()
            server_name = guild_data.get('name', 'Unknown Server')
        return server_name, channel_name
    except requests.exceptions.RequestException as e:
        log_message(f"Error mengambil info channel: {e}", "ERROR")
        return "Unknown Server", "Unknown Channel"

def get_bot_info(token):
    headers = {'Authorization': token}
    try:
        response = requests.get("https://discord.com/api/v9/users/@me", headers=headers)
        response.raise_for_status()
        data = response.json()
        username = data.get("username", "Unknown")
        discriminator = data.get("discriminator", "")
        bot_id = data.get("id", "Unknown")
        return username, discriminator, bot_id
    except requests.exceptions.RequestException as e:
        log_message(f"Gagal mengambil info akun bot: {e}", "ERROR")
        return "Unknown", "", "Unknown"

def is_valid_text_message(message_content):
    # Function to check if a message contains only valid text content
    # Filter out messages that contain links or are primarily emojis
    
    # Skip empty messages
    if not message_content or not message_content.strip():
        return False
    
    # Filter out URLs
    url_pattern = r'https?://\S+|www\.\S+'
    if re.search(url_pattern, message_content):
        return False
    
    # Filter out messages that are primarily emoji
    # This regex attempts to match common Unicode emoji patterns
    emoji_pattern = re.compile(
        "["
        u"\U0001F600-\U0001F64F"  # emoticons
        u"\U0001F300-\U0001F5FF"  # symbols & pictographs
        u"\U0001F680-\U0001F6FF"  # transport & map symbols
        u"\U0001F700-\U0001F77F"  # alchemical symbols
        u"\U0001F780-\U0001F7FF"  # Geometric Shapes
        u"\U0001F800-\U0001F8FF"  # Supplemental Arrows-C
        u"\U0001F900-\U0001F9FF"  # Supplemental Symbols and Pictographs
        u"\U0001FA00-\U0001FA6F"  # Chess Symbols
        u"\U0001FA70-\U0001FAFF"  # Symbols and Pictographs Extended-A
        u"\U00002702-\U000027B0"  # Dingbats
        u"\U000024C2-\U0001F251" 
        "]+", flags=re.UNICODE)
    
    # Count emojis in the message
    emoji_count = len(emoji_pattern.findall(message_content))
    text_length = len(message_content)
    
    # If more than 30% of the message is emojis, skip it
    if text_length > 0 and emoji_count / text_length > 0.3:
        return False
    
    # Filter out Discord emoji format like <:emoji_name:id>
    discord_emoji_pattern = r'<a?:[a-zA-Z0-9_]+:[0-9]+>'
    if re.search(discord_emoji_pattern, message_content):
        return False
    
    # Make sure the message has actual words (at least a few characters that aren't just symbols)
    if not re.search(r'[a-zA-Z0-9]{3,}', message_content):
        return False
    
    return True

def should_process_message(message_data, bot_user_id):
    """
    Determines if a message should be processed based on comprehensive criteria:
    - Ignores the bot's own messages
    - Processes standalone messages with no mentions
    - Processes replies to the bot's messages (for conversations)
    - Ignores replies to other users' messages
    - Ignores messages with mentions (except when replying to the bot)
    
    Returns True if the message should be processed, False otherwise
    """
    # Skip bot's own messages
    author_id = message_data.get('author', {}).get('id')
    if author_id == bot_user_id:
        return False
    
    # Check if message is a reply
    referenced_message = message_data.get('referenced_message')
    message_reference = message_data.get('message_reference')
    
    # If it's a reply, check if it's replying to the bot
    if referenced_message is not None or message_reference is not None:
        # If we have the full referenced message object
        if referenced_message is not None:
            ref_author_id = referenced_message.get('author', {}).get('id')
            # Only process if it's replying to our bot
            return ref_author_id == bot_user_id
        
        # If we only have message_reference but not the full referenced message
        # We can't determine who it's replying to, so let's skip to be safe
        return False
    
    # Check for mentions in the message content
    content = message_data.get('content', '')
    mention_pattern = r'<@!?[0-9]+>'
    if re.search(mention_pattern, content):
        # Check if the only mention is to our bot
        mentions = re.findall(mention_pattern, content)
        if len(mentions) == 1:
            # Extract the user ID from the mention
            mention_id = re.sub(r'[<@!>]', '', mentions[0])
            return mention_id == bot_user_id
        return False
    
    # Check for mentions array in the message
    mentions = message_data.get('mentions', [])
    if mentions:
        # If there's only one mention and it's our bot
        if len(mentions) == 1 and mentions[0].get('id') == bot_user_id:
            return True
        return False
    
    # If it's a standalone message with no mentions, process it
    return True

def auto_reply(channel_id, settings, token):
    headers = {'Authorization': token}
    if settings["use_google_ai"]:
        try:
            bot_info_response = requests.get('https://discord.com/api/v9/users/@me', headers=headers)
            bot_info_response.raise_for_status()
            bot_user_id = bot_info_response.json().get('id')
        except requests.exceptions.RequestException as e:
            log_message(f"[Channel {channel_id}] Gagal mengambil info bot: {e}", "ERROR")
            return

        while True:
            prompt = None
            reply_to_id = None
            log_message(f"[Channel {channel_id}] Menunggu {settings['read_delay']} detik sebelum membaca pesan...", "WAIT")
            time.sleep(settings["read_delay"])
            try:
                response = requests.get(f'https://discord.com/api/v9/channels/{channel_id}/messages', headers=headers)
                response.raise_for_status()
                messages = response.json()
                if messages:
                    most_recent_message = messages[0]
                    message_id = most_recent_message.get('id')
                    message_type = most_recent_message.get('type', '')
                    
                    if message_type != 8 and message_id not in processed_message_ids:
                        # Use the new function instead of is_standalone_message
                        if not should_process_message(most_recent_message, bot_user_id):
                            log_message(f"[Channel {channel_id}] Pesan dilewati (tidak memenuhi kriteria).", "WARNING")
                            processed_message_ids.add(message_id)
                            continue
                        
                        user_message = most_recent_message.get('content', '').strip()
                        attachments = most_recent_message.get('attachments', [])
                        
                        if attachments or not is_valid_text_message(user_message):
                            log_message(f"[Channel {channel_id}] Pesan dilewati (bukan teks murni atau mengandung link/emoji).", "WARNING")
                            processed_message_ids.add(message_id)
                        else:
                            log_message(f"[Channel {channel_id}] Received: {user_message}", "INFO")
                            if settings["use_slow_mode"]:
                                slow_mode_delay = get_slow_mode_delay(channel_id, token)
                                log_message(f"[Channel {channel_id}] Slow mode aktif, menunggu {slow_mode_delay} detik...", "WAIT")
                                time.sleep(slow_mode_delay)
                            prompt = user_message
                            reply_to_id = message_id
                            processed_message_ids.add(message_id)
                else:
                    prompt = None
            except requests.exceptions.RequestException as e:
                log_message(f"[Channel {channel_id}] Request error: {e}", "ERROR")
                prompt = None

            if prompt:
                # Get the user ID from the message
                user_id = most_recent_message.get('author', {}).get('id')
                
                # Get conversation history for this user
                conversation_history = get_conversation_history(user_id, channel_id)
                
                # Generate reply with conversation history
                result = generate_reply(prompt, settings["prompt_language"], settings["use_google_ai"], settings.get("persona"), conversation_history)
                
                if result is None:
                    log_message(f"[Channel {channel_id}] Bahasa prompt tidak valid. Pesan dilewati.", "WARNING")
                else:
                    response_text = result if result else "Maaf, tidak dapat membalas pesan."
                    if response_text.strip().lower() == prompt.strip().lower():
                        log_message(f"[Channel {channel_id}] Balasan sama dengan pesan yang diterima. Tidak mengirim balasan.", "WARNING")
                    else:
                        if settings["use_reply"]:
                            send_message(channel_id, response_text, token, reply_to=reply_to_id, 
                                         delete_after=settings["delete_bot_reply"], delete_immediately=settings["delete_immediately"])
                        else:
                            send_message(channel_id, response_text, token, 
                                         delete_after=settings["delete_bot_reply"], delete_immediately=settings["delete_immediately"])
                        
                        # Update conversation history after successful reply
                        update_conversation_history(user_id, channel_id, prompt, response_text)
            else:
                log_message(f"[Channel {channel_id}] Tidak ada pesan baru atau pesan tidak valid.", "INFO")

            log_message(f"[Channel {channel_id}] Menunggu {settings['delay_interval']} detik sebelum iterasi berikutnya...", "WAIT")
            time.sleep(settings["delay_interval"])
    else:
        while True:
            delay = settings["delay_interval"]
            log_message(f"[Channel {channel_id}] Menunggu {delay} detik sebelum mengirim pesan dari file...", "WAIT")
            time.sleep(delay)
            message_text = generate_reply("", settings["prompt_language"], use_google_ai=False)
            if settings["use_reply"]:
                send_message(channel_id, message_text, token, delete_after=settings["delete_bot_reply"], delete_immediately=settings["delete_immediately"])
            else:
                send_message(channel_id, message_text, token, delete_after=settings["delete_bot_reply"], delete_immediately=settings["delete_immediately"])

def send_message(channel_id, message_text, token, reply_to=None, delete_after=None, delete_immediately=False):
    headers = {'Authorization': token, 'Content-Type': 'application/json'}
    payload = {'content': message_text}
    if reply_to:
        payload["message_reference"] = {"message_id": reply_to}
    url = f"https://discord.com/api/v9/channels/{channel_id}/messages"
    try:
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        if response.status_code in [200, 201]:
            data = response.json()
            message_id = data.get("id")
            log_message(f"[Channel {channel_id}] Pesan terkirim: \"{message_text}\" (ID: {message_id})", "SUCCESS")
            if delete_after is not None:
                if delete_immediately:
                    log_message(f"[Channel {channel_id}] Menghapus pesan segera tanpa delay...", "WAIT")
                    threading.Thread(target=delete_message, args=(channel_id, message_id, token), daemon=True).start()
                elif delete_after > 0:
                    log_message(f"[Channel {channel_id}] Pesan akan dihapus dalam {delete_after} detik...", "WAIT")
                    threading.Thread(target=delayed_delete, args=(channel_id, message_id, delete_after, token), daemon=True).start()
        else:
            log_message(f"[Channel {channel_id}] Gagal mengirim pesan. Status: {response.status_code}", "ERROR")
            log_message(f"[Channel {channel_id}] Respons API: {response.text}", "ERROR")
    except requests.exceptions.RequestException as e:
        log_message(f"[Channel {channel_id}] Kesalahan saat mengirim pesan: {e}", "ERROR")

def delayed_delete(channel_id, message_id, delay, token):
    time.sleep(delay)
    delete_message(channel_id, message_id, token)

def delete_message(channel_id, message_id, token):
    headers = {'Authorization': token, 'Content-Type': 'application/json'}
    url = f'https://discord.com/api/v9/channels/{channel_id}/messages/{message_id}'
    try:
        response = requests.delete(url, headers=headers)
        if response.status_code == 204:
            log_message(f"[Channel {channel_id}] Pesan dengan ID {message_id} berhasil dihapus.", "SUCCESS")
        else:
            log_message(f"[Channel {channel_id}] Gagal menghapus pesan. Status: {response.status_code}", "ERROR")
            log_message(f"[Channel {channel_id}] Respons API: {response.text}", "ERROR")
    except requests.exceptions.RequestException as e:
        log_message(f"[Channel {channel_id}] Kesalahan saat menghapus pesan: {e}", "ERROR")

def get_slow_mode_delay(channel_id, token):
    headers = {'Authorization': token, 'Accept': 'application/json'}
    url = f"https://discord.com/api/v9/channels/{channel_id}"
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        slow_mode_delay = data.get("rate_limit_per_user", 0)
        log_message(f"[Channel {channel_id}] Slow mode delay: {slow_mode_delay} detik", "INFO")
        return slow_mode_delay
    except requests.exceptions.RequestException as e:
        log_message(f"[Channel {channel_id}] Gagal mengambil informasi slow mode: {e}", "ERROR")
        return 5

def start_cleanup_thread():
    def periodic_cleanup():
        while True:
            time.sleep(3600)  # Run cleanup every hour
            cleanup_expired_conversations()
            log_message("Cleaned up expired conversations", "INFO")
    
    cleanup_thread = threading.Thread(target=periodic_cleanup, daemon=True)
    cleanup_thread.start()

def get_server_settings(channel_id, channel_name):
    print(f"\nMasukkan pengaturan untuk channel {channel_id} (Nama Channel: {channel_name}):")
    use_google_ai = input("  Gunakan Google Gemini AI? (y/n): ").strip().lower() == 'y'
    
    persona = None
    if use_google_ai:
        use_persona = input("  Gunakan persona khusus? (y/n): ").strip().lower() == 'y'
        if use_persona:
            persona = input("  Masukkan deskripsi persona (contoh: 'a helpful assistant', 'a medieval knight', etc): ").strip()
            
        prompt_language = input("  Pilih bahasa prompt (en/id): ").strip().lower()
        if prompt_language not in ["en", "id"]:
            print("  Input tidak valid. Default ke 'id'.")
            prompt_language = "id"
        enable_read_message = True
        read_delay = int(input("  Masukkan delay membaca pesan (detik): "))
        delay_interval = int(input("  Masukkan interval (detik) untuk setiap iterasi auto reply: "))
        use_slow_mode = input("  Gunakan slow mode? (y/n): ").strip().lower() == 'y'
    else:
        prompt_language = input("  Pilih bahasa pesan dari file (en/id): ").strip().lower()
        if prompt_language not in ["en", "id"]:
            print("  Input tidak valid. Default ke 'id'.")
            prompt_language = "id"
        enable_read_message = False
        read_delay = 0
        delay_interval = int(input("  Masukkan delay (detik) untuk mengirim pesan dari file: "))
        use_slow_mode = False

    use_reply = input("  Kirim pesan sebagai reply? (y/n): ").strip().lower() == 'y'
    hapus_balasan = input("  Hapus balasan bot setelah beberapa detik? (y/n): ").strip().lower() == 'y'
    if hapus_balasan:
        delete_bot_reply = int(input("  Setelah berapa detik balasan dihapus? (0 untuk tidak, atau masukkan delay): "))
        delete_immediately = input("  Hapus pesan langsung tanpa delay? (y/n): ").strip().lower() == 'y'
    else:
        delete_bot_reply = None
        delete_immediately = False

    return {
        "prompt_language": prompt_language,
        "use_google_ai": use_google_ai,
        "enable_read_message": enable_read_message,
        "read_delay": read_delay,
        "delay_interval": delay_interval,
        "use_slow_mode": use_slow_mode,
        "use_reply": use_reply,
        "delete_bot_reply": delete_bot_reply,
        "delete_immediately": delete_immediately,
        "persona": persona  # Add the persona to the settings
    }

if __name__ == "__main__":
    bot_accounts = {}
    for token in discord_tokens:
        username, discriminator, bot_id = get_bot_info(token)
        bot_accounts[token] = {"username": username, "discriminator": discriminator, "bot_id": bot_id}
        log_message(f"Akun Bot: {username}#{discriminator} (ID: {bot_id})", "SUCCESS")

    # Input channel IDs dari user
    channel_ids = [cid.strip() for cid in input("Masukkan ID channel (pisahkan dengan koma jika lebih dari satu): ").split(",") if cid.strip()]

    token = discord_tokens[0]
    channel_infos = {}
    for channel_id in channel_ids:
        server_name, channel_name = get_channel_info(channel_id, token)
        channel_infos[channel_id] = {"server_name": server_name, "channel_name": channel_name}
        log_message(f"[Channel {channel_id}] Terhubung ke server: {server_name} | Nama Channel: {channel_name}", "SUCCESS")

    server_settings = {}
    for channel_id in channel_ids:
        channel_name = channel_infos.get(channel_id, {}).get("channel_name", "Unknown Channel")
        server_settings[channel_id] = get_server_settings(channel_id, channel_name)

    for cid, settings in server_settings.items():
        info = channel_infos.get(cid, {"server_name": "Unknown Server", "channel_name": "Unknown Channel"})
        hapus_str = ("Langsung" if settings['delete_immediately'] else 
                     (f"Dalam {settings['delete_bot_reply']} detik" if settings['delete_bot_reply'] and settings['delete_bot_reply'] > 0 else "Tidak"))
        persona_str = f"Persona = {settings.get('persona', 'Tidak ada')}, " if settings.get('persona') else ""
        log_message(
            f"[Channel {cid} | Server: {info['server_name']} | Channel: {info['channel_name']}] "
            f"Pengaturan: Gemini AI = {'Aktif' if settings['use_google_ai'] else 'Tidak'}, "
            f"{persona_str}"
            f"Bahasa = {settings['prompt_language'].upper()}, "
            f"Membaca Pesan = {'Aktif' if settings['enable_read_message'] else 'Tidak'}, "
            f"Delay Membaca = {settings['read_delay']} detik, "
            f"Interval = {settings['delay_interval']} detik, "
            f"Slow Mode = {'Aktif' if settings['use_slow_mode'] else 'Tidak'}, "
            f"Reply = {'Ya' if settings['use_reply'] else 'Tidak'}, "
            f"Hapus Pesan = {hapus_str}",
            "INFO"
        )

    # Start the conversation cleanup thread
    start_cleanup_thread()
    log_message("Started conversation cleanup thread to manage memory", "SUCCESS")
    
    # Print the conversation memory settings
    log_message(f"Conversation memory settings: Max length = {max_conversation_length}, Expiry = {conversation_expiry/60} minutes", "INFO")

    token_index = 0
    for channel_id in channel_ids:
        token = discord_tokens[token_index % len(discord_tokens)]
        token_index += 1
        bot_info = bot_accounts.get(token, {"username": "Unknown", "discriminator": "", "bot_id": "Unknown"})
        thread = threading.Thread(
            target=auto_reply,
            args=(channel_id, server_settings[channel_id], token)
        )
        thread.daemon = True
        thread.start()
        log_message(f"[Channel {channel_id}] Bot aktif: {bot_info['username']}#{bot_info['discriminator']} (Token: {token[:4]}{'...' if len(token) > 4 else token})", "SUCCESS")

    log_message("Bot sedang berjalan di beberapa server... Tekan CTRL+C untuk menghentikan.", "INFO")
    while True:
        time.sleep(10)
