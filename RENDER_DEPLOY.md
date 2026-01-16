# Deploy Referral Contest Bot to Render

## 🚀 Quick Deployment Guide

### Step 1: Prepare GitHub Repository
1. Create a new repository on GitHub
2. Upload all your bot files to the repository
3. Make sure `.env` is in `.gitignore` (already done)

### Step 2: Deploy on Render
1. Go to [render.com](https://render.com) and sign up
2. Click "New +" → "Web Service"
3. Connect your GitHub repository
4. Configure the service:
   - **Name**: `referral-contest-bot`
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python complete_bot.py`

### Step 3: Set Environment Variables
In Render dashboard, add these environment variables:
- `BOT_TOKEN` = `8303316398:AAHWGOvC1dxRfvW58yyaFmzUMF_TGevaNJQ`
- `BOT_USERNAME` = `refercontestBot`

### Step 4: Deploy
- Click "Create Web Service"
- Render will automatically build and deploy your bot
- Your bot will be live 24/7!

## 📊 Features After Deployment
- ✅ 24/7 uptime
- ✅ Automatic restarts if bot crashes
- ✅ Free tier (750 hours/month)
- ✅ Automatic deployments from GitHub
- ✅ Built-in logging and monitoring

## 🔧 Managing Your Bot
- **View logs**: Render dashboard → Logs tab
- **Restart bot**: Render dashboard → Manual Deploy
- **Update bot**: Push changes to GitHub (auto-deploys)
- **Monitor usage**: Render dashboard → Metrics

Your referral contest bot will be production-ready and accessible to users worldwide!
