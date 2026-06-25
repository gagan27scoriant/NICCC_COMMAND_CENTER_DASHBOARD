from flask import Flask, render_template, jsonify, send_from_directory, send_file, request, redirect, url_for, make_response
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from pymongo import MongoClient
import jwt
import datetime
import random
import time
import os
import json
import threading
import uuid
import sys
import torch
from typing import Any, Dict
from pathlib import Path
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
DATA_DIR = Path(__file__).resolve().parent / 'Data'
DATA_DIR.mkdir(parents=True, exist_ok=True)
VA_UPLOADS_DIR = DATA_DIR / 'va_uploads'
VA_OUTPUTS_DIR = DATA_DIR / 'va_outputs'
VA_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
VA_OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

FIS_DIR = Path(__file__).resolve().parent / "Forensic_Intelligence_System"
VT_DIR = FIS_DIR / "vehicle-tracking"
sys.path.insert(0, str(VT_DIR))
sys.path.insert(0, str(FIS_DIR / "parseq"))

try:
    from database.mongo import MongoManager
    from services.anpr_service import ANPRService
except ImportError as e:
    print(f"Failed to import ANPR modules: {e}")
    MongoManager = None
    ANPRService = None

ANPR_UPLOADS_DIR = DATA_DIR / 'anpr_uploads'
ANPR_OUTPUTS_DIR = DATA_DIR / 'anpr_outputs'
ANPR_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
ANPR_OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

DENSITY_UPLOADS_DIR = DATA_DIR / 'density_uploads'
DENSITY_OUTPUTS_DIR = DATA_DIR / 'density_outputs'
DENSITY_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
DENSITY_OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_VEHICLE_MODEL = FIS_DIR / "models" / "yolov8m.pt"
DEFAULT_PLATE_MODEL = (
    FIS_DIR
    / "ANPR-demo"
    / "vehicle license plate detection"
    / "runs"
    / "detect"
    / "ANPR"
    / "yolo26s_plate-2"
    / "weights"
    / "best.pt"
)
GPU_DEVICE = "cuda:0"

anpr_jobs = {}
anpr_jobs_lock = threading.Lock()

def _update_anpr_job(job_id: str, **updates: Any) -> None:
    with anpr_jobs_lock:
        anpr_jobs.setdefault(job_id, {}).update(updates)

def _anpr_job_snapshot(job_id: str) -> Dict[str, Any] | None:
    with anpr_jobs_lock:
        job = anpr_jobs.get(job_id)
        return dict(job) if job is not None else None

def _run_anpr(video_path: Path, output_video_path: Path | None = None) -> dict:
    if not torch.cuda.is_available():
        print("CUDA is required for this app, but no GPU is available in the active environment.")
    service = ANPRService(
        vehicle_model_path=str(DEFAULT_VEHICLE_MODEL),
        plate_model_path=str(DEFAULT_PLATE_MODEL),
        device=GPU_DEVICE,
    )
    return service.process_video(
        video_path=video_path,
        output_video_path=output_video_path,
        output_csv_path=None,
        visualise=False,
    )

def _process_anpr_job(job_id: str, video_path: Path) -> None:
    _update_anpr_job(job_id, status="RUNNING")
    try:
        annotated_path = ANPR_OUTPUTS_DIR / f"{job_id}_annotated.mp4"
        summary = _run_anpr(video_path, output_video_path=annotated_path)
        result = {
            **summary,
            "original_video_path": str(video_path),
            "annotated_video_path": str(annotated_path),
        }
        _update_anpr_job(job_id, status="COMPLETED", result=result, error=None)
    except Exception as exc:
        _update_anpr_job(job_id, status="FAILED", error=str(exc))

def _serialize_document(document):
    serialized = dict(document)
    if "_id" in serialized:
        serialized["_id"] = str(serialized["_id"])
    if isinstance(serialized.get("created_at"), datetime.datetime):
        serialized["created_at"] = serialized["created_at"].isoformat()
    return serialized
VA_OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

# Video Analytics state
va_state = {
    'latest_summary_path': None,
    'latest_video_path': None,
    'latest_clip_path': None,
    'latest_result': None,
    'events': []
}

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

# ═══════════════════════════════════════════════════════════════
#  Video Analytics API Routes (integrated from video_inttelligence)
# ═══════════════════════════════════════════════════════════════

def _generate_va_events(video_path, frame_count):
    """Generate simulated analytics events for the uploaded video."""
    event_types = ['crowd_count', 'fight', 'fall', 'crowd_surge', 'loitering']
    events = []
    num_events = random.randint(3, 8)
    for i in range(num_events):
        frame_idx = random.randint(0, max(1, frame_count - 1))
        event_type = random.choice(event_types)
        confidence = round(random.uniform(0.65, 0.98), 2)
        person_count = random.randint(1, 25)
        track_ids = [random.randint(1, 50) for _ in range(random.randint(1, 3))]
        roi_zones = ['Zone-A', 'Zone-B', 'Zone-C', 'Entrance', 'Exit', 'Platform', 'Corridor']
        event = {
            'event_type': event_type,
            'frame_index': frame_idx,
            'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'confidence': confidence,
            'person_count': person_count,
            'camera_id': f'CAM-VA-{random.randint(1, 8):02d}',
            'track_ids': track_ids,
            'roi_zone': random.choice(roi_zones),
            'snapshot_path': None,
            'metadata': {
                'mean_velocity': round(random.uniform(0.5, 8.0), 1),
                'elapsed_seconds': round(random.uniform(5.0, 120.0), 1),
                'importance': round(random.uniform(0.3, 1.0), 2)
            }
        }
        events.append(event)
    return sorted(events, key=lambda e: e['frame_index'])

def _build_va_narrative(result):
    """Create a readable narrative summary from events."""
    events = result.get('events', [])
    if not events:
        return {'title': 'No notable incident', 'dominant_event': None, 'importance': 0, 'counts': {}, 'top_events': []}
    counts = {}
    for e in events:
        et = e.get('event_type', 'unknown')
        counts[et] = counts.get(et, 0) + 1
    priority = {'fight': 4.0, 'fall': 3.0, 'crowd_surge': 2.5, 'loitering': 1.5, 'crowd_count': 1.0}
    scored = {}
    for e in events:
        et = e.get('event_type', 'unknown')
        score = priority.get(et, 0.5) + float(e.get('confidence', 0))
        scored[et] = max(scored.get(et, 0), score)
    dominant = max(scored.items(), key=lambda x: x[1])[0] if scored else None
    readable = {'crowd_count': 'Crowd monitoring', 'crowd_surge': 'Crowd surge', 'fight': 'Possible fight / aggression', 'fall': 'Possible fall', 'loitering': 'Loitering'}
    top = [n for n, _ in sorted(scored.items(), key=lambda x: x[1], reverse=True)[:3]]
    title = ' / '.join(readable.get(e, e) for e in top if e != 'crowd_count') or 'General activity'
    return {'title': title, 'dominant_event': dominant, 'importance': scored.get(dominant, 0), 'counts': counts, 'top_events': top}

def _process_va_video(video_path_str, summary_path_str):
    """Background thread: process video and write summary JSON."""
    try:
        import cv2
        cap = cv2.VideoCapture(video_path_str)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) if cap.isOpened() else 300
        cap.release()
    except Exception:
        frame_count = 300

    # Simulate processing delay
    time.sleep(random.uniform(2.0, 5.0))

    events = _generate_va_events(video_path_str, frame_count)
    result = {
        'events': events,
        'status': 'completed',
        'frames_processed': frame_count,
        'output_video': video_path_str,
        'output_video_ready': True,
        'highlight_clip': None,
        'highlight_clip_ready': False,
        'event_counts': {},
    }
    for e in events:
        et = e['event_type']
        result['event_counts'][et] = result['event_counts'].get(et, 0) + 1
    result['narrative'] = _build_va_narrative(result)

    with open(summary_path_str, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2)

    va_state['latest_result'] = result
    va_state['events'] = events

@app.route('/api/va/upload', methods=['POST'])
def va_upload():
    """Upload a video for analytics processing."""
    file = request.files.get('video')
    if not file:
        return jsonify({'error': 'video file required'}), 400
    filename = secure_filename(file.filename or f'{uuid.uuid4()}.mp4')
    video_path = VA_UPLOADS_DIR / filename
    file.save(str(video_path))
    summary_path = VA_OUTPUTS_DIR / f'{video_path.stem}_summary.json'
    va_state['latest_summary_path'] = str(summary_path)
    va_state['latest_video_path'] = str(video_path)
    va_state['latest_clip_path'] = None
    va_state['latest_result'] = {'status': 'processing', 'video': str(video_path), 'summary': str(summary_path), 'output_video': str(video_path)}
    va_state['events'] = []
    threading.Thread(
        target=_process_va_video,
        args=(str(video_path), str(summary_path)),
        daemon=True
    ).start()
    return jsonify({'status': 'processing', 'video': str(video_path), 'summary': str(summary_path)})

@app.route('/api/va/events')
def va_events():
    """Return recent analytics events."""
    return jsonify(va_state.get('events', []))

@app.route('/api/va/summary/latest')
def va_latest_summary():
    """Return the latest analytics summary."""
    result = va_state.get('latest_result')
    summary_path = va_state.get('latest_summary_path')
    if result and result.get('status') in {'completed', 'failed'}:
        return jsonify(result)
    if not summary_path:
        return jsonify({'error': 'no summary requested yet'}), 404
    path = Path(summary_path)
    if path.exists():
        try:
            with path.open('r', encoding='utf-8') as f:
                payload = json.load(f)
            va_state['latest_result'] = payload
            return jsonify(payload)
        except Exception:
            return jsonify({'status': 'processing', 'summary': summary_path}), 202
    return jsonify({'status': 'processing', 'summary': summary_path}), 202

@app.route('/api/va/video/latest')
def va_latest_video():
    """Serve the latest uploaded video."""
    video_path = va_state.get('latest_video_path')
    if not video_path:
        return jsonify({'error': 'no video uploaded yet'}), 404
    path = Path(video_path)
    if not path.exists() or path.stat().st_size < 1024:
        return jsonify({'status': 'processing'}), 202
    return send_file(str(path.resolve()), mimetype='video/mp4', as_attachment=False)

@app.route('/api/va/clip/latest')
def va_latest_clip():
    """Serve the latest highlight clip."""
    clip_path = va_state.get('latest_clip_path')
    if not clip_path:
        return jsonify({'error': 'no clip available'}), 404
    path = Path(clip_path)
    if not path.exists() or path.stat().st_size < 1024:
        return jsonify({'status': 'processing'}), 202
    return send_file(str(path.resolve()), mimetype='video/mp4', as_attachment=False)

@app.route('/api/va/reset', methods=['POST'])
def va_reset():
    """Reset video analytics state."""
    va_state['latest_summary_path'] = None
    va_state['latest_video_path'] = None
    va_state['latest_clip_path'] = None
    va_state['latest_result'] = None
    va_state['events'] = []
    return jsonify({'status': 'cleared'})

# ═══════════════════════════════════════════════════════════════
#  ANPR API Routes
# ═══════════════════════════════════════════════════════════════

@app.route('/api/anpr/upload', methods=['POST'])
def anpr_upload_video():
    uploaded_file = request.files.get("video")
    if uploaded_file is None or not uploaded_file.filename:
        return jsonify({"detail": "No video file uploaded."}), 400

    filename = secure_filename(uploaded_file.filename.lower())
    if not (
        filename.endswith(".mp4")
        or uploaded_file.mimetype in {"video/mp4", "application/octet-stream"}
    ):
        return jsonify({"detail": "Only MP4 video files are accepted."}), 400

    job_id = uuid.uuid4().hex
    destination_path = ANPR_UPLOADS_DIR / f"{job_id}.mp4"
    uploaded_file.save(str(destination_path))

    _update_anpr_job(job_id, status="PENDING", result=None, error=None)
    worker = threading.Thread(
        target=_process_anpr_job,
        args=(job_id, destination_path),
        daemon=True,
    )
    worker.start()

    return jsonify(
        {
            "job_id": job_id,
            "status_url": url_for("anpr_get_job_status", job_id=job_id),
            "evidence_url": url_for("anpr_get_evidence_by_video", video_id=job_id),
            "video_url": url_for("anpr_serve_upload", filename=f"{job_id}.mp4"),
            "annotated_video_url": url_for(
                "anpr_serve_output", filename=f"{job_id}_annotated.mp4"
            ),
        }
    )

@app.route('/api/anpr/job/<job_id>')
def anpr_get_job_status(job_id):
    job = _anpr_job_snapshot(job_id)
    if job is None:
        return jsonify({"detail": "Job not found."}), 404

    response = {"job_id": job_id, "status": job.get("status")}
    if job.get("status") == "COMPLETED":
        response["result"] = job.get("result", {})
    elif job.get("status") == "FAILED":
        response["error"] = job.get("error", "Unknown error")
    return jsonify(response)

@app.route('/api/anpr/evidence/video/<video_id>')
def anpr_get_evidence_by_video(video_id):
    if MongoManager is None:
        return jsonify({"detail": "MongoDB ANPR manager not available."}), 500
    mongo = MongoManager()
    mongo.connect()
    evidences = mongo.find_by_video(video_id)
    if not evidences:
        return jsonify({"detail": "No evidence for this video_id."}), 404
    return jsonify([_serialize_document(ev) for ev in evidences])

@app.route('/api/anpr/evidence/plate/<plate_number>')
def anpr_get_evidence_by_plate(plate_number):
    if MongoManager is None:
        return jsonify({"detail": "MongoDB ANPR manager not available."}), 500
    mongo = MongoManager()
    mongo.connect()
    evidences = mongo.find_by_plate(plate_number)
    if not evidences:
        return jsonify({"detail": "No evidence for this plate."}), 404
    return jsonify([_serialize_document(ev) for ev in evidences])

@app.route('/api/anpr/uploads/<path:filename>')
def anpr_serve_upload(filename):
    return send_from_directory(ANPR_UPLOADS_DIR, filename)

@app.route('/api/anpr/outputs/<path:filename>')
def anpr_serve_output(filename):
    return send_from_directory(ANPR_OUTPUTS_DIR, filename)

@app.route('/api/anpr/reset', methods=['POST'])
def anpr_reset():
    with anpr_jobs_lock:
        anpr_jobs.clear()
    return jsonify({'status': 'cleared'})

if __name__ == '__main__':
    app.run(debug=True, port=5000)
