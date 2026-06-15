from flask import Flask, render_template, jsonify, send_from_directory, request, redirect, url_for, make_response
from werkzeug.security import generate_password_hash, check_password_hash
from pymongo import MongoClient
import jwt
import datetime
import random
import time
import os
from functools import wraps

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('JWT_SECRET', os.urandom(24).hex())

# MongoDB Setup
try:
    client = MongoClient('mongodb://localhost:27017/', serverSelectionTimeoutMS=2000)
    db = client['niccc_db']
    users_collection = db['users']
    departments_collection = db['departments']
    permissions_collection = db['permissions']
    audit_collection = db['audit_logs']
    
    # Initialize default admin user if it doesn't exist
    if not users_collection.find_one({"email": "admin@123"}):
        hashed_password = generate_password_hash("admin@123")
        users_collection.insert_one({
            "email": "admin@123",
            "password": hashed_password,
            "role": "superuser",
            "created_at": datetime.datetime.now(datetime.timezone.utc)
        })
        print("Default admin user created.")
except Exception as e:
    print(f"MongoDB connection error: {e}")


VIDEO_DIR = os.path.join(os.path.dirname(__file__), 'video')

@app.route('/video/<path:filename>')
def serve_video(filename):
    return send_from_directory(VIDEO_DIR, filename)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        user = users_collection.find_one({"email": email})
        
        if user and check_password_hash(user['password'], password):
            # Generate JWT
            token = jwt.encode({
                'user': user['email'],
                'role': user['role'],
                'exp': datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=12)
            }, app.config['SECRET_KEY'], algorithm='HS256')
            
            resp = make_response(redirect(url_for('dashboard')))
            # Set JWT as HttpOnly cookie
            resp.set_cookie('niccc_token', token, httponly=True, samesite='Strict')
            return resp
        else:
            return render_template('login.html', error="Invalid credentials.")
            
    return render_template('login.html', error=None)

@app.route('/logout')
def logout():
    resp = make_response(redirect(url_for('login')))
    resp.delete_cookie('niccc_token')
    return resp

def get_current_user():
    token = request.cookies.get('niccc_token')
    if not token:
        return None
    try:
        data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
        return data
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

def superuser_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user_data = get_current_user()
        if not user_data or user_data.get('role') != 'superuser':
            return jsonify({"error": "Unauthorized access"}), 403
        return f(*args, **kwargs)
    return decorated_function

def log_audit(action, details):
    user_data = get_current_user()
    user = user_data['user'] if user_data else 'system'
    try:
        audit_collection.insert_one({
            "timestamp": datetime.datetime.now(datetime.timezone.utc),
            "user": user,
            "action": action,
            "details": details
        })
    except Exception as e:
        print(f"Failed to log audit: {e}")

@app.route('/')
def dashboard():
    user_data = get_current_user()
    if not user_data:
        return redirect(url_for('login'))
    return render_template('dashboard.html', user=user_data['user'], role=user_data['role'])

@app.route('/api/metrics')
def metrics():
    """Live metrics endpoint — poll every few seconds for real-time feel."""
    return jsonify({
        "cpu": random.randint(30, 85),
        "memory": random.randint(40, 78),
        "network": random.randint(200, 950),
        "threats": random.randint(0, 5),
        "uptime": "99.97%",
        "active_nodes": random.randint(140, 160),
        "bandwidth": round(random.uniform(1.2, 4.8), 1),
        "packets": random.randint(10000, 99999),
        "timestamp": int(time.time())
    })

@app.route('/api/camera_feed')
def camera_feed():
    """Simulated camera status data."""
    cams = []
    zones = ["Lobby A", "Server Room", "Parking B", "Exit Gate", "Data Center", "Roof Access", "Bay 3", "Control"]
    statuses = ["LIVE", "LIVE", "LIVE", "LIVE", "LIVE", "IDLE", "LIVE", "RECORDING"]
    for i, (zone, status) in enumerate(zip(zones, statuses)):
        cams.append({"id": i+1, "zone": zone, "status": status, "alert": random.choice([False, False, False, True])})
    return jsonify(cams)

# --- Admin API Routes ---

@app.route('/api/admin/users', methods=['GET', 'POST'])
@superuser_required
def admin_users():
    if request.method == 'GET':
        users = list(users_collection.find({}, {"_id": 0, "password": 0}))
        return jsonify(users)
    
    if request.method == 'POST':
        data = request.json
        email = data.get('email')
        role = data.get('role', 'operator')
        dept = data.get('department', 'General')
        password = data.get('password', 'temp@123')
        
        if users_collection.find_one({"email": email}):
            return jsonify({"error": "User already exists"}), 400
            
        hashed_password = generate_password_hash(password)
        users_collection.insert_one({
            "email": email,
            "password": hashed_password,
            "role": role,
            "department": dept,
            "created_at": datetime.datetime.now(datetime.timezone.utc)
        })
        log_audit("CREATE_USER", f"Created user {email} with role {role}")
        return jsonify({"success": True})

@app.route('/api/admin/departments', methods=['GET', 'POST'])
@superuser_required
def admin_departments():
    if request.method == 'GET':
        depts = list(departments_collection.find({}, {"_id": 0}))
        return jsonify(depts)
        
    if request.method == 'POST':
        data = request.json
        name = data.get('name')
        if not name:
            return jsonify({"error": "Department name required"}), 400
            
        departments_collection.insert_one({
            "name": name,
            "created_at": datetime.datetime.now(datetime.timezone.utc)
        })
        log_audit("CREATE_DEPT", f"Created department {name}")
        return jsonify({"success": True})

@app.route('/api/admin/permissions', methods=['GET', 'POST'])
@superuser_required
def admin_permissions():
    if request.method == 'GET':
        perms = list(permissions_collection.find({}, {"_id": 0}))
        return jsonify(perms)
        
    if request.method == 'POST':
        data = request.json
        role = data.get('role')
        access_level = data.get('access_level')
        
        permissions_collection.insert_one({
            "role": role,
            "access_level": access_level,
            "updated_at": datetime.datetime.now(datetime.timezone.utc)
        })
        log_audit("UPDATE_PERM", f"Updated permissions for role {role}")
        return jsonify({"success": True})

@app.route('/api/admin/audit', methods=['GET'])
@superuser_required
def admin_audit():
    logs = list(audit_collection.find({}, {"_id": 0}).sort("timestamp", -1).limit(50))
    return jsonify(logs)

if __name__ == '__main__':
    app.run(debug=True, port=5000)
