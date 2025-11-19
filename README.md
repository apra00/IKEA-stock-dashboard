# 🛒 IKEA Availability Monitor

A full-featured web application that checks stock availability for IKEA products using the open-source **ikea-availability-checker** Node tool.  
It provides real-time store-by-store availability, historical tracking, notifications, webhooks, and user/role management for your friends.

---

## ✨ Features

### ✔ Product Monitoring
- Add unlimited IKEA items
- Organize them into simple folders/categories
- Live per-store stock availability
- Historical tracking with chart
- Automatic stock checks (manual or webhook)
- Store lookup by country

### ✔ Notifications
- Email notifications when stock reaches a threshold
- Per-item notification toggle and threshold value
- SMTP settings in configuration

### ✔ Webhooks & API Keys
- Admin-only API key
- Secure webhook endpoint to trigger a stock check

### ✔ User Management
- Login system (Flask-Login)
- Roles: admin, editor
- Users can set their own email for notifications

---
## 🧩 Technology Stack

**Backend**
- Python 3  
- Flask  
- SQLAlchemy  
- Flask-Login  
- Flask-Migrate  
- smtplib (email)

**Frontend**
- Bootstrap 5  
- Chart.js  
- Jinja2 Templates

**IKEA Data Provider**
- Node.js  
- `ikea-availability-checker` NPM package

---

## 📦 Installation

### 1. Clone the repo
```bash
git clone https://github.com/apra00/IKEA-stock-dashboard.git
cd IKEA-stock-dashboard
```

### 2. Create virtual environment
```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Python dependencies
```bash
pip install -r requirements.txt
```

### 4. Install Node dependency
```bash
npm install ikea-availability-checker
```

### 5. Initialize the database
```bash
flask db upgrade
```

---

## ⚙️ Configuration

Create `.env` or configure environment variables or use the `config.py`:

### Flask
```
SECRET_KEY=your-secret
```

### SMTP (optional)
```
SMTP_SERVER=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=me@example.com
SMTP_PASSWORD=yourpassword
SMTP_FROM=me@example.com
SMTP_USE_TLS=True
```

### Admin API key (for webhooks)
```
WEBHOOK_API_KEY=yourapikey
```

### DEBUG toggle
Set it `true` to enable Flask debugging
```
FLASK_DEBUG=false
```

---

## 🚀 Running the App

### Development
```bash
flask run
```

---

## 🔁 Triggering a Webhook

Send:

```bash
curl -X POST https://yourdomain.com/webhook/check \
  -H "X-API-Key: YOUR_ADMIN_API_KEY"
```

If the key matches the admin API key, the app runs a stock check for all active items.

---

## ⏱ Cron Job

To auto-refresh stock:

```
*/30 * * * * curl -X POST -H "X-API-Key: YOUR_ADMIN_API_KEY" https://yourdomain.com/webhook/check
```

Runs every 30 minutes.

---

## 🤝 Contributing

Pull requests are welcome!  
If you'd like to add features (e.g., Telegram alerts, Docker support), feel free to open an issue.

---

## 📜 License

MIT — free for personal and commercial use.

---

## ❤️ Credits

- Uses the excellent [`ikea-availability-checker`](https://github.com/Ephigenia/ikea-availability-checker)
- Built with Flask, Bootstrap, and Chart.js

---

## 📬 Support

If you need help or want additional features, feel free to open an issue.
