from flask import Flask, render_template, jsonify, send_from_directory, request, redirect, url_for, session
import random
import time
import os

app = Flask(__name__)
app.secret_key = os.urandom(24)  # For session management

VIDEO_DIR = os.path.join(os.path.dirname(__file__), 'video')

@app.route('/video/<path:filename>')
def serve_video(filename):
    return send_from_directory(VIDEO_DIR, filename)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        # Default credentials with basic RBAC setup
        if email == 'admin@123' and password == 'admin@123':
            session['user'] = 'admin'
            session['role'] = 'superuser'
            return redirect(url_for('dashboard'))
        else:
            return render_template('login.html', error="Invalid credentials. Use admin@123")
            
    return render_template('login.html', error=None)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
def dashboard():
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template('dashboard.html', user=session['user'], role=session['role'])

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

if __name__ == '__main__':
    app.run(debug=True, port=5000)
