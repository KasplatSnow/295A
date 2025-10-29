#  Surveillance Detection System – Backend (Django + DRF)

This is the backend service for the **Surveillance Detection System**, built with **Django**, **Django REST Framework (DRF)**, and **SQLite (dev)**.

It exposes a multi-tenant REST API used by the React frontend to handle:
- User authentication (JWT)
- Tenants & memberships
- Cameras, incidents, detections, alerts
- Audit logging and reporting

---

##  Tech Stack

- **Python 3.12+**
- **Django 5+**
- **Django REST Framework**
- **SimpleJWT** (JWT authentication)
- **CORS Headers**
- **SQLite** (development)
- **PostgreSQL** (recommended for production)

---

##  Setup Instructions

### Create a virtual environment
Inside the terminal go into the backend folder and create a virtual env. 
```
python -m venv venv
source venv/bin/activate  # Linux / macOS
# OR
venv\Scripts\activate     # Windows

```
### Install dependencies

```pip install -r requirements.txt
```

### Create your own env file
Copy and paste contents from .env.example to .env file inside server/
```
cp .env.example .env

```
### Run Database Migrations
```
python manage.py makemigrations
python manage.py migrate
```

### Create a superuser(admin)
```
python manage.py createsuperuser
```