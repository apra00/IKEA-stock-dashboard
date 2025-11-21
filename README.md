# 🚀 IKEA Checker - The IKEA Stock Dashboard
<img width="163" height="41" alt="image" src="https://github.com/user-attachments/assets/3ef14c74-8933-419b-ab8e-1f4c2d591fdd" />

A modern, self-hosted dashboard for monitoring IKEA product availability in real time.  
Track items, receive alerts, view historical data, and integrate automated checks — all in a secure, multi-user environment.

---

## ✨ Features

- 🔍 **Track IKEA products** by product ID  
- 🏬 **Store-specific or country-wide checks**  
- 📬 **Stock threshold email alerts**  
- 📊 **Historical availability charts**  
- 🧑‍💼 **Admin panel** (user + item management)  
- 🔐 **Secure webhook** for automated background checks  
- 🧩 **Node.js integration** for live IKEA API data

---
<img width="963" height="1157" alt="image" src="https://github.com/user-attachments/assets/65d2b5a6-1f79-4f05-b157-91f770ac4c59" />
---

## 📦 Requirements

**Backend**
- Python **3.10+**
- Flask ecosystem (Login, SQLAlchemy, Limiter)

**Node**
- Node.js **18+**  
- `ikea-availability-checker` dependencies

**System**
- SMTP server for email notifications  
- SQLite (default) or any SQLAlchemy-compatible DB

---

## ⚙️ Installation

### 1️⃣ Clone the project
```bash
git clone https://github.com/apra00/IKEA-stock-dashboard.git
cd repo
```

### 2️⃣ Python environment
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3️⃣ Node dependencies
```bash
npm install
```

### 4️⃣ Add environment variables

Create **.env** in the project root:

```
SECRET_KEY=your-secret-key
WEBHOOK_API_KEY=your-webhook-key
DATABASE_URL=sqlite:///ikea_availability.db

SMTP_SERVER=smtp.example.com
SMTP_PORT=587
SMTP_USE_TLS=true
SMTP_USERNAME=your-email-user
SMTP_PASSWORD=your-email-password
SMTP_FROM=alerts@example.com
```

⚠️ `SECRET_KEY` and `WEBHOOK_API_KEY` **must** be set — they have no defaults.

---

## 🗄️ Database Setup

```bash
flask db upgrade
```

On first launch, an admin user is auto-created with a generated password printed in the console.

---

## ▶️ Running the App

### Development
```bash
export FLASK_APP=app
export FLASK_ENV=development
flask run
```

### Production Example (Gunicorn)
```bash
gunicorn -w 4 'app:create_app()'
```

---

## 🧭 Usage Guide

### ➕ Add an Item
1. Log in  
2. (Optional) Create a folder  
3. Add a product by **product ID**  
4. Choose country + store filters  
5. Set notification threshold (optional)

### 📈 View Item Data
- Live stock  
- Probability summary  
- Per-store availability  
- Historical trend chart  

### 🧑‍💼 Admin Tools
- Manage users  
- Manage all items  
- Trigger system-wide checks  

---

## 🌐 Webhook API (Automation)

### Endpoint
```
POST /api/check
Header: X-API-Key: <WEBHOOK_API_KEY>
```

### Payload Options

**Check all items**
```json
{}
```

**Check by internal item ID**
```json
{"item_id": 12}
```

**Check by IKEA product ID**
```json
{"product_id": "80213074"}
```

Returns JSON with results.

---

## 📁 Project Structure (simplified)

```
app/
  auth/           Authentication
  dashboard/      UI & charts
  items/          Item CRUD & views
  api/            Webhook endpoints
  users/          Admin management
  ikea_service.py Node integration + notifications
  models.py
  extensions.py

node/
  ikea_client.js
  ikea_stores.js
```

---

## 🔒 Security

- API key must be sent via **X-API-Key** header  
- Login is **rate limited**  
- Redirects sanitized to avoid open-redirect attacks  
- Node subprocesses **timeout automatically**  
- Email alerts only notify item owners (and admins if configured)

---

## 📜 License
MIT (or your chosen license)

---

## ❤️ Contributing
PRs welcome — feel free to extend notifications, add new alerts, integrate with Home Assistant, or improve the UI.

