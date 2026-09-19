import json
import io
import os
import uuid
from fastapi import FastAPI, HTTPException, Depends, Header, UploadFile, File, Form
from fastapi.responses import Response, StreamingResponse, FileResponse
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import Optional

from fastapi.middleware.cors import CORSMiddleware

from . import models, database, auth, schemas
from . import voice as voice_module
from . import ocr as ocr_module
from . import summary as summary_module
from . import red_flag_engine
from . import chatbot as chatbot_module
from . import hospitals as hospitals_module


app = FastAPI(
    title="Med-Drishti Backend",
    version="0.1.0",
    description="AI-powered clinical intake system"
)

# Enable CORS for frontend clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create tables at startup
models.Base.metadata.create_all(bind=database.engine)


def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
) -> models.User:
    """Get current authenticated user from JWT token"""
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    
    try:
        scheme, token = authorization.split()
        if scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid authorization scheme")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    
    payload = auth.decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    
    user_id = payload.get("sub")
    user = db.query(models.User).filter(models.User.id == int(user_id)).first() if user_id is not None else None
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    
    return user


def get_current_user_optional(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
) -> Optional[models.User]:
    """Return the authenticated user when a bearer token is supplied; otherwise None."""
    if not authorization:
        return None

    try:
        scheme, token = authorization.split()
        if scheme.lower() != "bearer":
            return None
    except ValueError:
        return None

    payload = auth.decode_access_token(token)
    if not payload:
        return None

    user_id = payload.get("sub")
    if user_id is None:
        return None

    user = db.query(models.User).filter(models.User.id == int(user_id)).first()
    return user


# ============ Health Check ============
@app.get("/api/v1/health")
def health():
    """Health check endpoint"""
    return {"status": "ok", "time": datetime.utcnow().isoformat()}


# ============ Auth Endpoints ============
@app.post("/api/v1/auth/register", response_model=schemas.TokenResponse)
def register(payload: schemas.UserRegister, db: Session = Depends(get_db)):
    """Register a new user"""
    # Check if user already exists
    existing_user = db.query(models.User).filter(models.User.email == payload.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Create new user
    user = models.User(
        email=payload.email,
        hashed_password=auth.hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    
    # Generate token
    access_token_expires = timedelta(minutes=auth.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = auth.create_access_token(
        data={"sub": str(user.id), "email": user.email},
        expires_delta=access_token_expires
    )
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": auth.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    }


@app.post("/api/v1/auth/login", response_model=schemas.TokenResponse)
def login(payload: schemas.UserLogin, db: Session = Depends(get_db)):
    """Login user and get access token"""
    user = db.query(models.User).filter(models.User.email == payload.email).first()
    if not user or not auth.verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    if not user.is_active:
        raise HTTPException(status_code=403, detail="User account is inactive")
    
    # Generate token
    access_token_expires = timedelta(minutes=auth.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = auth.create_access_token(
        data={"sub": str(user.id), "email": user.email},
        expires_delta=access_token_expires
    )
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": auth.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    }


@app.post("/api/v1/auth/logout")
def logout(current_user: models.User = Depends(get_current_user)):
    """Logout (token invalidation handled client-side)"""
    return {"message": "Logged out successfully"}


# ============ Patient Endpoints ============
@app.post("/api/v1/patients", response_model=schemas.PatientResponse)
def create_patient(
    payload: schemas.PatientCreate,
    current_user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    """Create a new patient. Allow a kiosk-style guest flow without a pre-existing auth account."""
    date_of_birth = payload.date_of_birth.strip() if payload.date_of_birth and payload.date_of_birth.strip() else None
    phone = payload.phone.strip() if payload.phone and payload.phone.strip() else None
    abha_id = payload.abha_id.strip() if payload.abha_id and payload.abha_id.strip() else None

    patient = models.Patient(
        user_id=current_user.id if current_user and current_user.role == models.RoleEnum.PATIENT else None,
        name=payload.name.strip(),
        date_of_birth=date_of_birth,
        gender=payload.gender,
        phone=phone,
        preferred_language=payload.preferred_language or "English",
        abha_id=abha_id,
    )
    db.add(patient)
    db.commit()
    db.refresh(patient)
    return patient


@app.get("/api/v1/patients/{patient_id}", response_model=schemas.PatientResponse)
def get_patient(
    patient_id: int,
    current_user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    """Get patient by ID"""
    patient = db.query(models.Patient).filter(models.Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    
    # Check authorization: patients can only view their own records
    if current_user and current_user.role == models.RoleEnum.PATIENT and patient.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    return patient


@app.put("/api/v1/patients/{patient_id}", response_model=schemas.PatientResponse)
def update_patient(
    patient_id: int,
    payload: schemas.PatientUpdate,
    current_user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    """Update patient record"""
    patient = db.query(models.Patient).filter(models.Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    
    # Check authorization
    if current_user and current_user.role == models.RoleEnum.PATIENT and patient.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Update fields
    update_data = payload.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(patient, field, value)
    
    db.commit()
    db.refresh(patient)
    return patient


# ============ Clinical Session Endpoints ============
@app.post("/api/v1/sessions", response_model=schemas.ClinicalSessionResponse)
def create_session(
    payload: schemas.ClinicalSessionCreate,
    patient_id: Optional[int] = None,
    current_user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    """Create a new clinical session. Support both query-param and JSON-based patient IDs."""
    session_patient_id = patient_id if patient_id is not None else payload.patient_id
    if session_patient_id is None:
        raise HTTPException(status_code=400, detail="patient_id is required")

    patient = db.query(models.Patient).filter(models.Patient.id == session_patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    
    # Check authorization
    if current_user and current_user.role == models.RoleEnum.PATIENT and patient.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    session = models.ClinicalSession(
        patient_id=session_patient_id,
        session_type=payload.session_type
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


@app.get("/api/v1/sessions/{session_id}", response_model=schemas.ClinicalSessionResponse)
def get_session(
    session_id: int,
    current_user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    """Get clinical session by ID"""
    session = db.query(models.ClinicalSession).filter(models.ClinicalSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Check authorization
    if current_user and current_user.role == models.RoleEnum.PATIENT and session.patient.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    return session


@app.put("/api/v1/sessions/{session_id}", response_model=schemas.ClinicalSessionResponse)
def update_session(
    session_id: int,
    payload: schemas.ClinicalSessionUpdate,
    current_user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    """Update clinical session status"""
    session = db.query(models.ClinicalSession).filter(models.ClinicalSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Check authorization (doctors/nurses can update)
    if current_user and current_user.role == models.RoleEnum.PATIENT:
        raise HTTPException(status_code=403, detail="Access denied")
    
    if payload.status:
        session.status = payload.status
        if payload.status == "completed":
            session.completed_at = datetime.utcnow()
    
    db.commit()
    db.refresh(session)
    return session


# ============ Clinical History Endpoints ============
@app.post("/api/v1/sessions/{session_id}/history", response_model=schemas.ClinicalHistoryResponse)
def create_clinical_history(
    session_id: int,
    payload: schemas.ClinicalHistoryCreate,
    current_user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    """Create or update clinical history for a session"""
    session = db.query(models.ClinicalSession).filter(models.ClinicalSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Check authorization
    if current_user and current_user.role == models.RoleEnum.PATIENT and session.patient.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    history = models.ClinicalHistory(
        session_id=session_id,
        chief_complaint=payload.chief_complaint,
        history_of_present_illness=payload.history_of_present_illness,
        past_medical_history=payload.past_medical_history,
        medications=payload.medications,
        allergies=payload.allergies,
        family_history=payload.family_history,
        social_history=payload.social_history,
    )
    db.add(history)
    db.commit()
    db.refresh(history)

    # Evaluate red flags on input text
    combined_text = f"{payload.chief_complaint or ''} {payload.history_of_present_illness or ''} {payload.medications or ''} {payload.allergies or ''}"
    triggered_flags = red_flag_engine.evaluate_red_flags(combined_text)
    for tf in triggered_flags:
        rf = models.RedFlag(
            session_id=session_id,
            rule_id=tf["rule_id"],
            description=tf["description"],
            severity=tf["severity"]
        )
        db.add(rf)
    if triggered_flags:
        db.commit()

    return history


@app.get("/api/v1/sessions/{session_id}/history", response_model=list[schemas.ClinicalHistoryResponse])
def get_clinical_histories(
    session_id: int,
    current_user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    """Get all clinical histories for a session"""
    session = db.query(models.ClinicalSession).filter(models.ClinicalSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Check authorization
    if current_user and current_user.role == models.RoleEnum.PATIENT and session.patient.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    histories = db.query(models.ClinicalHistory).filter(models.ClinicalHistory.session_id == session_id).all()
    return histories


# ============ Consent Endpoints ============
@app.post("/api/v1/patients/{patient_id}/consents", response_model=schemas.ConsentResponse)
def create_consent(
    patient_id: int,
    payload: schemas.ConsentCreate,
    current_user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    """Create a consent record"""
    patient = db.query(models.Patient).filter(models.Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    
    # Check authorization
    if current_user and current_user.role == models.RoleEnum.PATIENT and patient.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    consent = models.Consent(
        patient_id=patient_id,
        consent_type=payload.consent_type,
        status=models.ConsentStatusEnum.ACCEPTED,  # Auto-accept for now
        signed_at=datetime.utcnow(),
        expires_at=payload.expires_at,
    )
    db.add(consent)
    db.commit()
    db.refresh(consent)
    return consent


@app.get("/api/v1/patients/{patient_id}/consents", response_model=list[schemas.ConsentResponse])
def get_consents(
    patient_id: int,
    current_user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    """Get all consents for a patient"""
    patient = db.query(models.Patient).filter(models.Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    
    # Check authorization
    if current_user and current_user.role == models.RoleEnum.PATIENT and patient.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    consents = db.query(models.Consent).filter(models.Consent.patient_id == patient_id).all()
    return consents


# ============ Voice Endpoints ============

_POLICY_PATH = os.path.join(os.path.dirname(__file__), "dialogue_policy.json")

def _get_dialogue_policy():
    with open(_POLICY_PATH, encoding="utf-8") as f:
        policy = json.load(f)
    q_map = {q["id"]: q for q in policy["questions"]}
    return policy, q_map


@app.get("/api/v1/voice/tts")
def generate_tts_audio(text: str, lang: str = "en"):
    """Generate high quality native MP3 audio stream for any language text."""
    if not text:
        raise HTTPException(status_code=400, detail="Text parameter is required")
    
    lang_map = {
        "bn": "bn",
        "ta": "ta",
        "ml": "ml",
        "te": "te",
        "pa": "pa",
        "hi": "hi",
        "en": "en"
    }
    target_lang = lang_map.get(lang.lower(), "en")
    
    try:
        from gtts import gTTS
        tts = gTTS(text=text.strip(), lang=target_lang)
        mp3_fp = io.BytesIO()
        tts.write_to_fp(mp3_fp)
        mp3_fp.seek(0)
        return Response(content=mp3_fp.getvalue(), media_type="audio/mpeg")
    except Exception as e:
        print(f"[Backend TTS Error] {e}")
        raise HTTPException(status_code=500, detail=f"TTS generation failed: {str(e)}")




@app.post("/api/v1/voice/transcribe", response_model=schemas.VoiceTranscribeResponse)
async def transcribe_voice(
    audio: UploadFile = File(...),
    language: str = Form(default="en"),
    current_user: Optional[models.User] = Depends(get_current_user_optional),
):
    """Transcribe uploaded audio to text using Whisper."""
    audio_bytes = await audio.read()
    result = voice_module.transcribe_audio(audio_bytes, language_hint=language if language != "en" else None)
    return schemas.VoiceTranscribeResponse(**result)


@app.post("/api/v1/voice/next-question", response_model=schemas.NextQuestionResponse)
def get_next_question(
    payload: schemas.NextQuestionRequest,
    current_user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """Get the next dialogue question for a session."""
    # Verify session exists
    session = db.query(models.ClinicalSession).filter(
        models.ClinicalSession.id == payload.session_id
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    policy, question_map = _get_dialogue_policy()

    # Determine next question id
    if payload.current_question_id is None:
        # Start from beginning
        next_id = policy["start_question_id"]
    else:
        current_q = question_map.get(payload.current_question_id)
        if not current_q:
            raise HTTPException(status_code=400, detail=f"Unknown question id: {payload.current_question_id}")
        next_id = current_q.get("next")

    # If no next question, dialogue is done
    if next_id is None:
        return schemas.NextQuestionResponse(
            question_id=None,
            question_text=None,
            field=None,
            done=True,
            session_id=payload.session_id,
        )

    next_q = question_map.get(next_id)
    if not next_q:
        raise HTTPException(status_code=500, detail=f"Question {next_id} not found in policy")

    # Select language-specific text if available
    lang = payload.language
    text_key = f"text_{lang}"
    question_text = next_q.get(text_key) or next_q["text"]

    return schemas.NextQuestionResponse(
        question_id=next_q["id"],
        question_text=question_text,
        field=next_q["field"],
        done=False,
        session_id=payload.session_id,
    )


# ============ Document & OCR Endpoints (Phase 4) ============
@app.post("/api/v1/documents", response_model=schemas.DocumentDetailResponse)
async def upload_document(
    session_id: int = Form(...),
    file: UploadFile = File(...),
    current_user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    """Upload a document, perform OCR, and extract medical entities."""
    session = db.query(models.ClinicalSession).filter(models.ClinicalSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Save file locally
    upload_dir = os.path.join(os.path.dirname(__file__), "..", "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, f"{session_id}_{file.filename}")

    file_bytes = await file.read()
    with open(file_path, "wb") as f:
        f.write(file_bytes)

    # OCR text extraction
    raw_ocr_text = ocr_module.extract_ocr_text(file_path)

    # Save Document DB record
    doc_record = models.Document(
        session_id=session_id,
        file_name=file.filename,
        file_type=file.content_type or "application/octet-stream",
        s3_key=file_path,
        ocr_text=raw_ocr_text
    )
    db.add(doc_record)
    db.commit()
    db.refresh(doc_record)

    # Extract Entities
    extracted = ocr_module.extract_entities_from_text(raw_ocr_text)
    entity_records = []
    for ent in extracted:
        entity_obj = models.ExtractedEntity(
            document_id=doc_record.id,
            entity_type=ent["entity_type"],
            entity_value=ent["entity_value"],
            confidence=ent["confidence"],
            source_text=ent["source_text"]
        )
        db.add(entity_obj)
        entity_records.append(entity_obj)

    db.commit()
    db.refresh(doc_record)

    # Evaluate red flags on document OCR text
    triggered = red_flag_engine.evaluate_red_flags(raw_ocr_text)
    for tf in triggered:
        rf = models.RedFlag(
            session_id=session_id,
            rule_id=tf["rule_id"],
            description=tf["description"],
            severity=tf["severity"]
        )
        db.add(rf)
    if triggered:
        db.commit()

    return doc_record


@app.get("/api/v1/sessions/{session_id}/documents", response_model=list[schemas.DocumentDetailResponse])
def get_session_documents(
    session_id: int,
    current_user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    """Get all uploaded documents & extracted entities for a session."""
    session = db.query(models.ClinicalSession).filter(models.ClinicalSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return session.documents


# ============ Medical Record Endpoints ============
@app.post("/api/v1/patients/{patient_id}/medical-records", response_model=schemas.MedicalRecordResponse)
async def upload_medical_record(
    patient_id: int,
    file: UploadFile = File(...),
    title: str = Form(default=""),
    description: str = Form(default=""),
    record_type: str = Form(default="other"),
    session_id: Optional[int] = Form(default=None),
    current_user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    """Upload an old medical record (image, PDF, report) for a patient."""
    patient = db.query(models.Patient).filter(models.Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    # Save file locally
    upload_dir = os.path.join(os.path.dirname(__file__), "..", "uploads", "medical_records")
    os.makedirs(upload_dir, exist_ok=True)
    unique_name = f"{patient_id}_{uuid.uuid4().hex[:8]}_{file.filename}"
    file_path = os.path.join(upload_dir, unique_name)

    file_bytes = await file.read()
    with open(file_path, "wb") as f:
        f.write(file_bytes)

    # Run OCR if it's an image/PDF
    ocr_text = ""
    try:
        ocr_text = ocr_module.extract_ocr_text(file_path)
    except Exception as e:
        print(f"[OCR Warning] Could not extract text from medical record: {e}")

    # Map record type string to enum
    try:
        rec_type = models.MedicalRecordTypeEnum(record_type)
    except ValueError:
        rec_type = models.MedicalRecordTypeEnum.OTHER

    record = models.MedicalRecord(
        patient_id=patient_id,
        session_id=session_id,
        record_type=rec_type,
        title=title.strip() if title else file.filename,
        description=description.strip() if description else None,
        file_name=file.filename,
        file_type=file.content_type or "application/octet-stream",
        file_path=file_path,
        ocr_text=ocr_text,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@app.get("/api/v1/patients/{patient_id}/medical-records", response_model=list[schemas.MedicalRecordResponse])
def get_patient_medical_records(
    patient_id: int,
    current_user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    """List all medical records for a patient."""
    patient = db.query(models.Patient).filter(models.Patient.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    records = db.query(models.MedicalRecord).filter(
        models.MedicalRecord.patient_id == patient_id
    ).order_by(models.MedicalRecord.uploaded_at.desc()).all()
    return records


@app.get("/api/v1/medical-records/{record_id}/file")
def get_medical_record_file(
    record_id: int,
    current_user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    """Download / view the uploaded medical record file."""
    record = db.query(models.MedicalRecord).filter(models.MedicalRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Medical record not found")
    if not record.file_path or not os.path.exists(record.file_path):
        raise HTTPException(status_code=404, detail="File not found on disk")

    return FileResponse(
        path=record.file_path,
        filename=record.file_name,
        media_type=record.file_type or "application/octet-stream",
    )


@app.delete("/api/v1/medical-records/{record_id}", status_code=200)
def delete_medical_record(
    record_id: int,
    current_user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    """Delete a medical record."""
    record = db.query(models.MedicalRecord).filter(models.MedicalRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Medical record not found")

    # Remove file from disk
    if record.file_path and os.path.exists(record.file_path):
        os.remove(record.file_path)

    db.delete(record)
    db.commit()
    return {"message": "Medical record deleted", "id": record_id}


# ============ Summary Generator Endpoints (Phase 5) ============
@app.get("/api/v1/sessions/{session_id}/summary")
def get_session_summary(
    session_id: int,
    current_user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    """Generate synthesized clinical summary payload for a session."""
    session = db.query(models.ClinicalSession).filter(models.ClinicalSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    patient_dict = {
        "id": session.patient.id,
        "name": session.patient.name,
        "gender": session.patient.gender,
        "date_of_birth": session.patient.date_of_birth,
        "preferred_language": session.patient.preferred_language
    }

    history_rec = session.histories[-1] if session.histories else None
    history_dict = None
    if history_rec:
        history_dict = {
            "chief_complaint": history_rec.chief_complaint,
            "history_of_present_illness": history_rec.history_of_present_illness,
            "medications": history_rec.medications,
            "allergies": history_rec.allergies
        }

    docs_list = []
    for doc in session.documents:
        doc_dict = {
            "id": doc.id,
            "file_name": doc.file_name,
            "ocr_text": doc.ocr_text,
            "extracted_entities": [
                {
                    "id": ent.id,
                    "entity_type": ent.entity_type,
                    "entity_value": ent.entity_value,
                    "confidence": ent.confidence,
                    "source_text": ent.source_text
                }
                for ent in doc.extracted_entities
            ]
        }
        docs_list.append(doc_dict)

    red_flags_list = [
        {
            "id": rf.id,
            "rule_id": rf.rule_id,
            "description": rf.description,
            "severity": rf.severity,
            "reviewed": rf.reviewed
        }
        for rf in session.red_flags
    ]

    # Collect medical records for the patient
    medical_records_list = [
        {
            "id": mr.id,
            "record_type": mr.record_type.value if mr.record_type else "other",
            "title": mr.title,
            "description": mr.description,
            "file_name": mr.file_name,
            "ocr_text": mr.ocr_text,
            "uploaded_at": mr.uploaded_at.isoformat() if mr.uploaded_at else None
        }
        for mr in session.patient.medical_records
    ]

    summary = summary_module.generate_clinical_summary(
        patient=patient_dict,
        history=history_dict,
        documents=docs_list,
        red_flags=red_flags_list
    )

    # Attach medical records to the summary response
    summary["medical_records"] = medical_records_list
    return summary


# ============ Red-Flag Engine & Triage Endpoints (Phase 6) ============
@app.get("/api/v1/triage/alerts", response_model=list[schemas.RedFlagResponse])
def get_triage_alerts(
    current_user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    """Get active red flags for triage nurse dashboard."""
    alerts = db.query(models.RedFlag).order_by(models.RedFlag.triggered_at.desc()).all()
    return alerts


@app.put("/api/v1/triage/alerts/{alert_id}/review", response_model=schemas.RedFlagResponse)
def review_triage_alert(
    alert_id: int,
    payload: schemas.RedFlagReviewRequest,
    current_user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    """Mark a red flag as reviewed by triage nurse / physician."""
    alert = db.query(models.RedFlag).filter(models.RedFlag.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Red flag alert not found")

    alert.reviewed = payload.reviewed
    db.commit()
    db.refresh(alert)
    return alert


# ============ Doctor Dashboard & Verification Endpoints (Phase 7) ============
@app.get("/api/v1/doctor/queue", response_model=list[schemas.DoctorQueueItemResponse])
def get_doctor_queue(
    current_user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    """Get active and completed patient sessions queue for doctor review."""
    sessions = db.query(models.ClinicalSession).order_by(models.ClinicalSession.started_at.desc()).all()
    queue = []
    for s in sessions:
        red_flags = s.red_flags
        triage_status = "CRITICAL" if any(rf.severity == models.RedFlagSeverityEnum.CRITICAL for rf in red_flags) \
                        else ("HIGH" if any(rf.severity == models.RedFlagSeverityEnum.HIGH for rf in red_flags) else "STABLE")

        queue.append({
            "session_id": s.id,
            "patient_id": s.patient.id,
            "patient_name": s.patient.name,
            "patient_gender": s.patient.gender,
            "patient_dob": s.patient.date_of_birth,
            "session_type": s.session_type,
            "status": s.status,
            "started_at": s.started_at,
            "completed_at": s.completed_at,
            "triage_status": triage_status,
            "red_flags_count": len(red_flags),
            "documents_count": len(s.documents),
            "medical_records_count": len(s.patient.medical_records)
        })

    # Sort critical first, then active, then timestamp
    severity_rank = {"CRITICAL": 0, "HIGH": 1, "STABLE": 2}
    queue.sort(key=lambda x: (0 if x["status"] == "active" else 1, severity_rank[x["triage_status"]]))
    return queue


@app.put("/api/v1/sessions/{session_id}/verify")
def verify_session(
    session_id: int,
    payload: schemas.SessionVerifyRequest,
    current_user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    """Verify and sign off on a clinical session (creates AuditLog entry)."""
    session = db.query(models.ClinicalSession).filter(models.ClinicalSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Update clinical history if provided
    if session.histories:
        history = session.histories[-1]
        if payload.chief_complaint:
            history.chief_complaint = payload.chief_complaint
        if payload.history_of_present_illness:
            history.history_of_present_illness = payload.history_of_present_illness
        if payload.medications:
            history.medications = payload.medications
        if payload.allergies:
            history.allergies = payload.allergies

    # Mark session completed
    session.status = "completed"
    session.completed_at = datetime.utcnow()

    # Create AuditLog record
    audit_entry = models.AuditLog(
        patient_id=session.patient_id,
        action="verify_session",
        resource_type="clinical_session",
        resource_id=session.id,
        performed_by_user_id=current_user.id if current_user else None,
        details=f"Physician verified session #{session.id}. Notes: {payload.physician_notes or 'None'}"
    )
    db.add(audit_entry)
    db.commit()
    db.refresh(session)

    return {"message": "Session successfully verified and completed", "session_id": session.id, "status": "completed"}


@app.get("/api/v1/sessions/{session_id}/audit-logs", response_model=list[schemas.AuditLogResponse])
def get_session_audit_logs(
    session_id: int,
    current_user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    """Get audit logs for a session/patient."""
    session = db.query(models.ClinicalSession).filter(models.ClinicalSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    logs = db.query(models.AuditLog).filter(models.AuditLog.patient_id == session.patient_id).order_by(models.AuditLog.timestamp.desc()).all()
    return logs


# ============ Sarvam AI Chatbot Endpoint ============

class ChatMessage(schemas.BaseModel):
    role: str  # 'user' or 'assistant'
    content: str

class ChatRequest(schemas.BaseModel):
    messages: list[ChatMessage]
    language: str = "en"

class ChatResponse(schemas.BaseModel):
    reply: str
    language: str

@app.post("/api/v1/chat", response_model=ChatResponse)
def patient_chat(
    payload: ChatRequest,
):
    """Sarvam AI powered patient health assistant chatbot."""
    messages_dicts = [{"role": m.role, "content": m.content} for m in payload.messages]
    result = chatbot_module.get_chat_response(messages_dicts, language=payload.language)
    return ChatResponse(reply=result["reply"], language=result["language"])


# ============ Hospital Finder Endpoints ============

@app.get("/api/v1/hospitals/nearby")
def get_nearby_hospitals(
    lat: float,
    lng: float,
    radius_km: float = 50.0,
    limit: int = 10,
    hospital_type: Optional[str] = None,
):
    """Find hospitals near a given latitude/longitude (Haversine search)."""
    results = hospitals_module.get_nearby_hospitals(
        lat=lat, lng=lng, radius_km=radius_km, limit=limit, hospital_type=hospital_type
    )
    return {"hospitals": results, "count": len(results)}


@app.get("/api/v1/hospitals")
def list_hospitals(state: Optional[str] = None):
    """List all hospitals, optionally filtered by state."""
    results = hospitals_module.get_all_hospitals(state=state)
    return {"hospitals": results, "count": len(results)}


@app.get("/api/v1/hospitals/search")
def search_hospitals(q: str):
    """Full-text search hospitals by name, city, state, or specialty."""
    results = hospitals_module.search_hospitals(query=q)
    return {"hospitals": results, "count": len(results)}
