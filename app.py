from flask import Flask, render_template, jsonify
import random
import time

app = Flask(__name__)

@app.route('/')
def dashboard():
    return render_template('dashboard.html')

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
