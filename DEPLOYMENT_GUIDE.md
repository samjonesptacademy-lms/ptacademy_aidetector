# PT Academy AI Detector - Deployment Guide

## Deployment Architecture

Your setup:
- **VPS**: Hostinger KVM2 (2 CPU, 8GB RAM, 100GB disk)
- **Current**: n8n running as Docker container
- **New**: PT Detector Flask app (also Docker)
- **Frontend**: Cloudflare Pages (static files from GitHub)

```
┌──────────────────────────────────────────────────────────────┐
│ Your Hostinger VPS                                           │
│                                                              │
│  Nginx (reverse proxy on port 80/443)                       │
│  ├─→ n8n.yourdomain.com  → n8n container                    │
│  └─→ api.yourdomain.com  → PT Detector container            │
│                                                              │
│  Docker Containers (isolated, no communication):             │
│  ├─ n8n (AI Agent for learners)                             │
│  └─ PT Detector (Flask app + Zero GPT API)                  │
└──────────────────────────────────────────────────────────────┘
        ↑                                    ↑
        │ (traffic routing)                 │ (API calls)
        │                                    │
  Cloudflare Pages                    Cloudflare Pages
  (Frontend static files)             (calls API endpoint)
```

---

## Before You Start Tomorrow

### Information You'll Need:
1. **Your domain(s)**:
   - n8n domain: `n8n.yourdomain.com` (or current setup)
   - PT Detector API: `api.yourdomain.com` (or choose another)
   - Frontend: Same domain or different?

2. **Your Zero GPT API Key** (should be in `.env` already)

3. **SSH access to VPS** (you should already have this)

### Current VPS Status Check
When you're ready tomorrow, SSH in and run:
```bash
docker ps
```
This will show what's currently running and help confirm setup.

---

## Tomorrow's Deployment Steps (Overview)

### Step 1: Get Current Docker Setup
```bash
ssh root@your-vps-ip
docker ps  # See what's running
docker-compose config  # Check current docker-compose setup
```

### Step 2: Add PT Detector to Docker Compose
We'll add PT Detector as a new service alongside n8n in the same docker-compose.yml

### Step 3: Configure Nginx Routing
Set up Nginx to route:
- `api.yourdomain.com` → PT Detector (port 5000)
- `n8n.yourdomain.com` → n8n (existing port)

### Step 4: Deploy Frontend to Cloudflare Pages
Push static files to GitHub → Link to Cloudflare Pages → Auto-deploy

### Step 5: Update Frontend API Endpoint
Change API calls to point to `https://api.yourdomain.com`

### Step 6: Test & Go Live
Everything should be working!

---

## Files We'll Create Tomorrow

You'll need these files:

1. **`Dockerfile`** - Container definition for PT Detector
2. **`docker-compose.yml`** - Orchestrates both n8n + PT Detector
3. **`nginx.conf`** - Routes traffic to both services
4. **`.env.production`** - Production environment variables
5. **Frontend files for Cloudflare Pages**:
   - `index.html` (production version)
   - `static/logo.png`
   - `.gitignore`

---

## Resource Usage Estimate

With your 8GB RAM:
- **n8n**: ~800MB (typical usage)
- **PT Detector**: ~150MB
- **Nginx**: ~30MB
- **System**: ~1.5GB
- **Available**: ~5.5GB ✅ Plenty of headroom

No resource conflicts expected.

---

## Key Decisions to Make Tomorrow

1. **Domain names** - What subdomains do you want?
   - Example: `api.detector.yourdomain.com`?
   - Or: `pt-detector.yourdomain.com`?
   - Or: `api.yourdomain.com`?

2. **SSL/TLS** - Let's Encrypt (free) or Cloudflare SSL?
   - Recommendation: Let's Encrypt via certbot

3. **Zero GPT API Key** - Already in your `.env`?
   - We'll use this for production deployment

4. **GitHub repos** - Will you use:
   - One repo for everything?
   - Separate repos for frontend vs backend?
   - Recommendation: Separate is cleaner

---

## Quick Reference: Current Project Status

✅ **Backend ready**:
- app.py configured for Zero GPT API
- Learner name field added
- PDF reports with recommendations
- PT Academy branding complete
- Cleaned up unnecessary files
- Documentation updated

✅ **Frontend ready**:
- Rebranded with PT Academy colors
- Learner name input field
- Updated header badge
- API integration points defined

⏳ **Next: Deployment**:
- Docker containerization
- Nginx reverse proxy setup
- Cloudflare Pages frontend
- SSL/TLS certificates

---

## Resources & Links

- **Hostinger VPS Docs**: Check your Hostinger panel for SSH details
- **Docker**: https://docs.docker.com/compose/
- **Nginx**: https://nginx.org/en/docs/
- **Cloudflare Pages**: https://pages.cloudflare.com/
- **Let's Encrypt**: https://letsencrypt.org/

---

## Tomorrow's Timeline

Estimated time:
- **Setup & testing**: 30-45 minutes
- **DNS/SSL**: 15-20 minutes
- **Cloudflare Pages setup**: 10 minutes
- **Testing & verification**: 15 minutes
- **Total**: ~90 minutes (with coffee breaks)

---

## Questions to Answer Before Deployment

Write these down before starting tomorrow:

```
1. Primary domain: ________________________
2. API subdomain: ________________________
3. n8n current domain: ____________________
4. Zero GPT API Key location: .env? ✓
5. GitHub username: _______________________
6. Prefer separate GitHub repos? Yes/No
7. SSL preference: Let's Encrypt / Cloudflare
```

---

## Emergency Contacts

If something breaks:
- **n8n won't start**: Check Docker logs: `docker logs n8n`
- **PT Detector won't start**: Check Docker logs: `docker logs pt-detector`
- **Nginx issues**: Check config: `nginx -t`
- **Port conflicts**: Check what's using ports: `lsof -i`

---

## Next Steps

1. ✅ Save this file
2. ✅ Note your domain names
3. ✅ Tomorrow: Run setup following Step 1 above
4. ✅ Share VPS status and I'll provide exact files

**See you tomorrow! 🚀**
