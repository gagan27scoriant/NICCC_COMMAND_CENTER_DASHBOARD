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

# ── Forensic Intelligence System (ANPR & FRS) ────────────────────────────────
FIS_DIR = Path(__file__).resolve().parent / "Forensic_Intelligence_System"
VT_DIR = FIS_DIR / "vehicle-tracking"
FRS_DIR = FIS_DIR / "FRS"
sys.path.insert(0, str(VT_DIR))
sys.path.insert(0, str(FIS_DIR / "parseq"))
sys.path.insert(0, str(FRS_DIR))

# ── Video Intelligence (real VA pipeline) ────────────────────────────────────
VI_PROJECT_DIR = Path(__file__).resolve().parent / "video_inttelligence" / "project"
sys.path.insert(0, str(VI_PROJECT_DIR))

# Load .env for the video_analytics Settings before importing it
from dotenv import load_dotenv as _load_dotenv
_load_dotenv(VI_PROJECT_DIR / ".env")

try:
    from video_analytics.config import Settings as VASettings
    from video_analytics.main import build_count_event
    from video_analytics.models.event import AnalyticsEvent, EventType
    from video_analytics.output.db_writer import DBWriter
    from video_analytics.output.redis_publisher import EventPublisher
    from video_analytics.pipeline.capture import FrameCapture
    from video_analytics.pipeline.detector import Detector as VADetector
    from video_analytics.pipeline.incident import IncidentDetector
    from video_analytics.pipeline.snapshot import SnapshotSaver
    from video_analytics.pipeline.tracker import Tracker as VATracker
    from video_analytics.pipeline.vlm_summary import VLMVideoSummarizer
    VA_BACKEND_AVAILABLE = True
    print("[INFO] Real Video Analytics backend loaded successfully.")
except ImportError as _va_err:
    VA_BACKEND_AVAILABLE = False
    print(f"[WARN] Real VA backend not available, using simulation fallback: {_va_err}")

try:
    from database.mongo import MongoManager
    from services.anpr_service import ANPRService
except ImportError as e:
    print(f"Failed to import ANPR modules: {e}")
    MongoManager = None
    ANPRService = None

try:
    from core import FrsConfig, FRSPipeline, MongoEvidenceStore as FrsMongoStore
except ImportError as e:
    print(f"Failed to import FRS modules: {e}")
    FrsConfig = None
    FRSPipeline = None
    FrsMongoStore = None

ANPR_UPLOADS_DIR = DATA_DIR / 'anpr_uploads'
ANPR_OUTPUTS_DIR = DATA_DIR / 'anpr_outputs'
ANPR_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
ANPR_OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

FRS_UPLOADS_DIR = DATA_DIR / 'frs_uploads'
FRS_OUTPUTS_DIR = DATA_DIR / 'frs_outputs'
FRS_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
FRS_OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

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
    if ANPRService is None:
        raise RuntimeError("ANPR service unavailable: missing dependencies (e.g. 'supervision').")
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

frs_jobs = {}
frs_jobs_lock = threading.Lock()

def _frs_store():
    """Return a FrsMongoStore connected to the configured MongoDB instance."""
    cfg = FrsConfig()
    return FrsMongoStore(cfg.mongo_uri, cfg.mongo_db, cfg.mongo_collection)

def _update_frs_job(job_id: str, **updates: Any) -> None:
    with frs_jobs_lock:
        frs_jobs.setdefault(job_id, {}).update(updates)

def _frs_job_snapshot(job_id: str) -> Dict[str, Any] | None:
    with frs_jobs_lock:
        job = frs_jobs.get(job_id)
        return dict(job) if job is not None else None

def _run_frs(video_path: Path, output_video_path: Path | None = None) -> dict:
    if FRSPipeline is None:
        raise RuntimeError("FRS service unavailable.")
    if not torch.cuda.is_available():
        print("CUDA is required for this app, but no GPU is available in the active environment.")
    config = FrsConfig()
    pipeline = FRSPipeline(config)
    res = pipeline.process_video(video_path, output_path=output_video_path)
    return {
        "video_id": res.video_id,
        "frame_count": res.frame_count,
        "subject_count": res.subject_count,
        "track_count": res.track_count,
        "face_detections": res.face_detections,
        "observations": res.observations,
    }

def _process_frs_job(job_id: str, video_path: Path) -> None:
    _update_frs_job(job_id, status="RUNNING")
    try:
        annotated_path = FRS_OUTPUTS_DIR / f"{job_id}_annotated.mp4"
        summary = _run_frs(video_path, output_video_path=annotated_path)
        result = {
            **summary,
            "original_video_path": str(video_path),
            "annotated_video_path": str(annotated_path),
        }
        _update_frs_job(job_id, status="COMPLETED", result=result, error=None)
    except Exception as exc:
        _update_frs_job(job_id, status="FAILED", error=str(exc))

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

# Shared EventPublisher instance (in-memory, no Redis dependency)
_va_publisher: 'EventPublisher | None' = EventPublisher() if VA_BACKEND_AVAILABLE else None

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

def _build_event_counts(events):
    counts = {}
    for e in events:
        et = str(e.get('event_type', 'unknown'))
        counts[et] = counts.get(et, 0) + 1
    return counts


def _write_highlight_clip(video_path_str, output_path_str, events, seconds_before=2.0, seconds_after=4.0):
    """Write a short highlight clip around the strongest incident."""
    import cv2
    if not events:
        return
    best = events[0]
    frame_index = int(best.get('frame_index', 0))
    cap = cv2.VideoCapture(video_path_str)
    if not cap.isOpened():
        return
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if w <= 0 or h <= 0:
        cap.release()
        return
    start_f = max(0, int(frame_index - seconds_before * fps))
    end_f = int(frame_index + seconds_after * fps)
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_f)
    temp_path_str = str(output_path_str) + ".temp.mp4"
    writer = cv2.VideoWriter(temp_path_str, cv2.VideoWriter_fourcc(*'mp4v'), fps, (w, h))
    if not writer.isOpened():
        cap.release()
        return
    cf = start_f
    while cf <= end_f:
        ok, frame = cap.read()
        if not ok:
            break
        label = f"Highlight: {best.get('event_type', 'event')} | frame {frame_index}"
        cv2.rectangle(frame, (8, 8), (min(w - 8, 520), 60), (0, 0, 0), -1)
        cv2.putText(frame, label, (18, 42), cv2.FONT_HERSHEY_SIMPLEX, 0.85, (0, 255, 255), 2, cv2.LINE_AA)
        writer.write(frame)
        cf += 1
    writer.release()
    cap.release()

    import subprocess, os
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", temp_path_str,
            "-vcodec", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast",
            str(output_path_str)
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.path.exists(temp_path_str):
            os.remove(temp_path_str)
    except Exception as e:
        print(f"[VA CLIP TRANSCODE ERROR] {e}")
        if os.path.exists(temp_path_str):
            os.replace(temp_path_str, str(output_path_str))


def _process_va_video_real(video_path_str, summary_path_str, highlight_clip_path_str):
    """Background thread: process video using the REAL video_analytics pipeline."""
    import asyncio as _asyncio
    import cv2

    async def _runner():
        settings = VASettings()
        publisher = _va_publisher
        db_writer = DBWriter(settings.MONGODB_URL, settings.MONGODB_DB)
        try:
            await db_writer.startup()
            await db_writer.start()
        except Exception as _db_err:
            print(f"[WARN] VA DB writer startup failed (non-fatal): {_db_err}")
            db_writer = None

        try:
            capture = FrameCapture(video_path_str, settings.FRAME_SKIP)
            detector = VADetector(settings.YOLO_MODEL_PATH, settings.DETECTION_CONFIDENCE)
            tracker = VATracker(settings.REID_EVERY_N_FRAMES)
            incident_detector = IncidentDetector(
                settings.CAMERA_ID,
                settings.parsed_roi_zones(),
                settings.LOITER_SECONDS,
                settings.SURGE_VELOCITY_THRESHOLD,
                settings.FALL_COOLDOWN_SECONDS,
                settings.FIGHT_COOLDOWN_SECONDS,
            )
            snapshot = SnapshotSaver(settings.SNAPSHOT_DIR)
            vlm_summarizer = VLMVideoSummarizer(settings.OLLAMA_URL, settings.OLLAMA_VLM_MODEL)

            collected = []
            density_samples = []
            incident_rankings = []
            frame_counter = 0

            async for frame_idx, frame in capture.frames():
                frame_counter += 1
                detections = detector.detect(frame)
                tracks = tracker.update(detections, frame, frame_idx)
                incidents = incident_detector.analyze(tracks, frame_idx)
                count_event = build_count_event(
                    settings.CAMERA_ID, tracks, frame_idx, settings.CROWD_COUNT_THRESHOLD
                )
                events = ([count_event] if count_event else []) + incidents
                current_people = len(tracks)
                # Determine primary action label
                primary_action = 'crowd_monitoring'
                priority_order = ['fight', 'fall', 'crowd_surge', 'loitering', 'crowd_count']
                for ptype in priority_order:
                    for ev in events:
                        if str(getattr(ev, 'event_type', '')).lower().endswith(ptype):
                            primary_action = ptype
                            break
                    else:
                        continue
                    break
                density_samples.append({
                    'frame_index': frame_idx,
                    'person_count': current_people,
                    'action': primary_action,
                    'threshold': settings.CROWD_COUNT_THRESHOLD,
                    'threshold_crossed': current_people >= settings.CROWD_COUNT_THRESHOLD,
                    'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                })
                for event in events:
                    try:
                        event.snapshot_path = await snapshot.save(frame, event)
                    except Exception:
                        pass
                    if publisher:
                        try:
                            await publisher.publish(event)
                        except Exception:
                            pass
                    if db_writer:
                        try:
                            await db_writer.enqueue(event)
                        except Exception:
                            pass
                    payload = event.model_dump(mode='json')
                    collected.append(payload)
                    incident_rankings.append(payload)

            # Priority sort for key incidents
            priority_map = {'fight': 4.0, 'fall': 3.0, 'crowd_surge': 2.5, 'loitering': 1.5, 'crowd_count': 1.0}
            def _score_event(ev):
                et = str(ev.get('event_type', '')).split('.')[-1].lower()
                s = priority_map.get(et, 0.0)
                s += float(ev.get('confidence', 0.0))
                s += float((ev.get('metadata') or {}).get('importance', 0.0))
                s += float((ev.get('metadata') or {}).get('mean_velocity', 0.0)) / 10.0
                return s
            key_incidents = sorted(incident_rankings, key=_score_event, reverse=True)[:3]

            # Build density alerts
            density_alerts = []
            prev_count = 0
            for sample in density_samples:
                pc = int(sample.get('person_count', 0))
                if prev_count < settings.CROWD_COUNT_THRESHOLD <= pc:
                    density_alerts.append({
                        'frame_index': sample.get('frame_index'),
                        'person_count': pc,
                        'timestamp': sample.get('timestamp'),
                        'action': sample.get('action'),
                    })
                prev_count = pc

            result = {
                'events': collected,
                'status': 'completed',
                'frames_processed': frame_counter,
                'output_video': video_path_str,
                'output_video_ready': True,
                'highlight_clip': highlight_clip_path_str,
                'highlight_clip_ready': False,
                'event_counts': _build_event_counts(collected),
                'density_samples': density_samples,
                'density_alerts': density_alerts,
                'crowd_threshold': settings.CROWD_COUNT_THRESHOLD,
                'key_incidents': key_incidents,
            }
            result['video_summary'] = vlm_summarizer.summarize_video(video_path_str)
            result['summary_status'] = 'generated' if result['video_summary'] else 'missing'
            result['narrative'] = _build_va_narrative(result)
            _write_highlight_clip(video_path_str, highlight_clip_path_str, key_incidents)
            if Path(highlight_clip_path_str).exists() and Path(highlight_clip_path_str).stat().st_size >= 1024:
                result['highlight_clip_ready'] = True
            return result

        except Exception as exc:
            print(f"[ERROR] VA pipeline failed: {exc}")
            return {'status': 'failed', 'error': str(exc), 'video': video_path_str}
        finally:
            if db_writer:
                try:
                    await db_writer.drain()
                except Exception:
                    pass

    result = _asyncio.run(_runner())
    with open(summary_path_str, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, default=str)
    va_state['latest_result'] = result
    va_state['events'] = result.get('events', [])
    print(f"[INFO] VA pipeline completed: status={result.get('status')}, frames={result.get('frames_processed', 0)}")


def _process_va_video_sim(video_path_str, summary_path_str):
    """Fallback: simulated video analytics (used when real backend unavailable)."""
    try:
        import cv2
        cap = cv2.VideoCapture(video_path_str)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) if cap.isOpened() else 300
        cap.release()
    except Exception:
        frame_count = 300
    time.sleep(random.uniform(2.0, 4.0))
    events = _generate_va_events(video_path_str, frame_count)
    result = {
        'events': events,
        'status': 'completed',
        'frames_processed': frame_count,
        'output_video': video_path_str,
        'output_video_ready': True,
        'highlight_clip': None,
        'highlight_clip_ready': False,
        'event_counts': _build_event_counts(events),
    }
    result['narrative'] = _build_va_narrative(result)
    with open(summary_path_str, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2)
    va_state['latest_result'] = result
    va_state['events'] = events


def _process_va_video(video_path_str, summary_path_str):
    """Entry point for the background VA thread — uses real pipeline when available."""
    if VA_BACKEND_AVAILABLE:
        highlight_clip_path_str = str(
            VA_OUTPUTS_DIR / (Path(video_path_str).stem + '_highlight.mp4')
        )
        va_state['latest_clip_path'] = highlight_clip_path_str
        _process_va_video_real(video_path_str, summary_path_str, highlight_clip_path_str)
    else:
        _process_va_video_sim(video_path_str, summary_path_str)

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
    if _va_publisher:
        try:
            import asyncio
            asyncio.run(_va_publisher.clear())
        except Exception:
            pass
    return jsonify({'status': 'cleared'})

@app.route('/api/va/density/latest')
def va_density_latest():
    """Return the density curve and alerts from the latest VA result."""
    result = va_state.get('latest_result')
    if not result:
        return jsonify({'error': 'no analysis available yet'}), 404
    return jsonify({
        'crowd_threshold': result.get('crowd_threshold', 10),
        'density_curve': result.get('density_samples', []),
        'density_alerts': result.get('density_alerts', []),
        'status': result.get('status', 'unknown'),
    })

@app.route('/api/va/config/thresholds', methods=['POST'])
def va_config_thresholds():
    """Update crowd threshold dynamically."""
    data = request.get_json(silent=True) or {}
    new_thresh = int(data.get('crowd_threshold', 10))
    result = va_state.get('latest_result')
    if result:
        result['crowd_threshold'] = new_thresh
        samples = result.get('density_samples', [])
        alerts = []
        prev = 0
        for s in samples:
            pc = int(s.get('person_count', 0))
            if prev < new_thresh <= pc:
                alerts.append(s)
            prev = pc
        result['density_alerts'] = alerts
    return jsonify({'status': 'ok', 'crowd_threshold': new_thresh})

@app.route('/api/va/status')
def va_status():
    """Return VA backend availability and current job status."""
    result = va_state.get('latest_result')
    return jsonify({
        'backend': 'real' if VA_BACKEND_AVAILABLE else 'simulation',
        'job_status': result.get('status') if result else 'idle',
        'frames_processed': result.get('frames_processed', 0) if result else 0,
        'events_detected': len(va_state.get('events', [])),
    })

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

# ═══════════════════════════════════════════════════════════════
#  FRS API Routes
# ═══════════════════════════════════════════════════════════════

@app.route('/api/frs/upload', methods=['POST'])
def frs_upload_video():
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
    destination_path = FRS_UPLOADS_DIR / f"{job_id}.mp4"
    uploaded_file.save(str(destination_path))

    _update_frs_job(job_id, status="PENDING", result=None, error=None)
    worker = threading.Thread(
        target=_process_frs_job,
        args=(job_id, destination_path),
        daemon=True,
    )
    worker.start()

    return jsonify(
        {
            "job_id": job_id,
            "status_url": url_for("frs_get_job_status", job_id=job_id),
            "evidence_url": url_for("frs_get_evidence_by_video", video_id=job_id),
            "video_url": url_for("frs_serve_upload", filename=f"{job_id}.mp4"),
            "annotated_video_url": url_for(
                "frs_serve_output", filename=f"{job_id}_annotated.mp4"
            ),
        }
    )

@app.route('/api/frs/job/<job_id>')
def frs_get_job_status(job_id):
    job = _frs_job_snapshot(job_id)
    if job is None:
        return jsonify({"detail": "Job not found."}), 404

    response = {"job_id": job_id, "status": job.get("status")}
    if job.get("status") == "COMPLETED":
        response["result"] = job.get("result", {})
    elif job.get("status") == "FAILED":
        response["error"] = job.get("error", "Unknown error")
    return jsonify(response)

@app.route('/api/frs/evidence/video/<video_id>')
def frs_get_evidence_by_video(video_id):
    if FrsMongoStore is None:
        return jsonify({"detail": "MongoDB FRS store not available."}), 500
    store = _frs_store()
    raw_obs = store.list_observations(video_id=video_id, limit=5000)
    if not raw_obs:
        return jsonify([])

    grouped = {}
    for obs in raw_obs:
        tid = obs.get("track_id")
        sid = obs.get("subject_id", f"track_{tid}")
        key = (sid, tid)
        if key not in grouped:
            grouped[key] = {
                "subject_id": sid,
                "subject_label": obs.get("subject_label", sid),
                "track_id": tid,
                "first_seen_frame": obs.get("frame_index"),
                "last_seen_frame": obs.get("frame_index"),
                "best_face_score": obs.get("face_score"),
                "best_person_score": obs.get("person_score"),
                "face_crop_path": obs.get("face_crop_path"),
                "person_crop_path": obs.get("person_crop_path"),
                "observations_count": 1,
            }
        else:
            g = grouped[key]
            g["observations_count"] += 1
            fidx = obs.get("frame_index")
            if fidx is not None:
                if g["first_seen_frame"] is None or fidx < g["first_seen_frame"]:
                    g["first_seen_frame"] = fidx
                if g["last_seen_frame"] is None or fidx > g["last_seen_frame"]:
                    g["last_seen_frame"] = fidx

            fscore = obs.get("face_score")
            if fscore is not None and (g["best_face_score"] is None or fscore > g["best_face_score"]):
                g["best_face_score"] = fscore
                if obs.get("face_crop_path"):
                    g["face_crop_path"] = obs.get("face_crop_path")

            pscore = obs.get("person_score")
            if pscore is not None and (g["best_person_score"] is None or pscore > g["best_person_score"]):
                g["best_person_score"] = pscore
                if obs.get("person_crop_path"):
                    g["person_crop_path"] = obs.get("person_crop_path")

    results = list(grouped.values())
    results.sort(key=lambda x: x["track_id"] if x["track_id"] is not None else 0)
    return jsonify([_serialize_document(r) for r in results])

@app.route('/api/frs/uploads/<path:filename>')
def frs_serve_upload(filename):
    return send_from_directory(FRS_UPLOADS_DIR, filename)

@app.route('/api/frs/outputs/<path:filename>')
def frs_serve_output(filename):
    return send_from_directory(FRS_OUTPUTS_DIR, filename)

@app.route('/api/frs/media/<path:filename>')
def frs_serve_media(filename):
    clean = filename
    while clean.startswith("media/"):
        clean = clean[len("media/"):]
    return send_from_directory(FRS_DIR / "runtime" / "media", clean)

@app.route('/api/frs/reset', methods=['POST'])
def frs_reset():
    with frs_jobs_lock:
        frs_jobs.clear()
    return jsonify({'status': 'cleared'})

@app.route('/api/frs/similar-persons')
def frs_similar_persons():
    """Return cross-video linked identity groups."""
    if FrsMongoStore is None:
        return jsonify([])
    store = _frs_store()
    groups = store.list_subject_groups(limit=200)
    linked = []
    for idx, grp in enumerate(groups, start=1):
        if not grp.get('linked'):
            continue
        observations = store.get_subject_observations(str(grp.get('subject_id', '')), limit=8)
        observations.sort(
            key=lambda o: (float(o.get('face_score') or 0), float(o.get('person_score') or 0)),
            reverse=True,
        )
        best_face = next((o.get('face_crop_path') for o in observations if o.get('face_crop_path')), None)
        best_person = next((o.get('person_crop_path') for o in observations if o.get('person_crop_path')), None)
        videos_seen = [str(v) for v in grp.get('videos', []) if v]
        best_score = 0.0
        for o in observations:
            for k in ('face_score', 'person_score'):
                try:
                    best_score = max(best_score, float(o.get(k) or 0))
                except (TypeError, ValueError):
                    pass
        linked.append({
            **_serialize_document(grp),
            'person_display_id': f'person_{idx:04d}',
            'videos_seen': videos_seen,
            'videos_seen_label': ', '.join(videos_seen) if videos_seen else '—',
            'best_similarity': round(best_score, 4) if best_score else None,
            'best_face_crop_path': best_face,
            'best_person_crop_path': best_person,
        })
    return jsonify(linked)

@app.route('/api/frs/identities', methods=['GET'])
def frs_list_identities():
    """List all named identities from MongoDB."""
    if FrsMongoStore is None:
        return jsonify([])
    store = _frs_store()
    identities = store.list_identities(limit=500)
    subject_groups = {str(g.get('subject_id')): g for g in store.list_subject_groups(limit=500)}
    enriched = []
    for ident in identities:
        sid = str(ident.get('subject_id', ''))
        grp = subject_groups.get(sid, {})
        enriched.append({
            **_serialize_document(ident),
            'video_count': grp.get('video_count', 0),
            'observation_count': grp.get('observations', 0),
            'best_face_crop_path': grp.get('sample_face_crop_path'),
        })
    return jsonify(enriched)

@app.route('/api/frs/identities', methods=['POST'])
def frs_upsert_identity():
    """Create or update a named identity label."""
    if FrsMongoStore is None:
        return jsonify({'detail': 'MongoDB FRS store not available.'}), 500
    payload = request.get_json(silent=True) or request.form.to_dict() or {}
    subject_id = str(payload.get('subject_id', '')).strip()
    display_name = str(payload.get('display_name', '')).strip()
    notes = payload.get('notes')
    assigned_by = payload.get('assigned_by')
    if not subject_id or not display_name:
        return jsonify({'detail': 'subject_id and display_name are required.'}), 400
    store = _frs_store()
    record = store.upsert_identity(subject_id, display_name, notes=notes, assigned_by=assigned_by)
    return jsonify(record or {'subject_id': subject_id, 'display_name': display_name})

@app.route('/api/frs/search')
def frs_person_search():
    """Full-text search across subject groups in MongoDB."""
    if FrsMongoStore is None:
        return jsonify([])
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify([])
    store = _frs_store()
    results = store.search_people(query, limit=100)
    return jsonify([_serialize_document(r) for r in results])

@app.route('/api/frs/subjects')
def frs_list_subjects():
    """Return all subject groups (named or unnamed) for the identity editor."""
    if FrsMongoStore is None:
        return jsonify([])
    store = _frs_store()
    groups = store.list_subject_groups(limit=500)
    return jsonify([_serialize_document(g) for g in groups])

if __name__ == '__main__':
    app.run(debug=True, port=5000)

