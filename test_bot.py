import os
from dotenv import load_dotenv
import asyncio
import httpx

# Load environment variables
load_dotenv()

async def test_bot_token():
    """Simple test to verify bot token works."""
    bot_token = os.getenv('BOT_TOKEN')
    
    if not bot_token:
        print("❌ BOT_TOKEN not found in .env file")
        return False
    
    print(f"🔍 Testing bot token: {bot_token[:10]}...")
    
    try:
        # Test the bot token with a simple API call
        url = f"https://api.telegram.org/bot{bot_token}/getMe"
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            
        if response.status_code == 200:
            data = response.json()
            if data.get('ok'):
                bot_info = data.get('result', {})
                print(f"✅ Bot token is valid!")
                print(f"🤖 Bot name: {bot_info.get('first_name')}")
                print(f"📱 Bot username: @{bot_info.get('username')}")
                return True
            else:
                print(f"❌ Bot API returned error: {data.get('description')}")
                return False
        else:
            print(f"❌ HTTP error: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Connection error: {e}")
        return False

if __name__ == "__main__":
    result = asyncio.run(test_bot_token())
    if result:
        print("\n🎉 Bot token is working! You can now run the main bot.")
    else:
        print("\n🔧 Please check your bot token and internet connection.")
