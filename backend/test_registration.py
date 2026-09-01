# test_registration.py
import sys
from fastapi.testclient import TestClient
from app.main import app

def run_tests():
    out_lines = []
    def log(*args):
        line = " ".join(str(a) for a in args)
        out_lines.append(line)

    client = TestClient(app)

    # 1. Test CORS POST request with Origin header
    r_cors = client.post(
        '/api/v1/patients',
        headers={'Origin': 'http://localhost:3000'},
        json={
            'name': 'Test CORS Patient',
            'preferred_language': 'English'
        }
    )
    log("POST CORS Status:", r_cors.status_code)
    log("POST CORS Allow Origin Header:", r_cors.headers.get('access-control-allow-origin'))

    # 2. Test Patient Registration with empty string abha_id / optional fields
    r_p1 = client.post(
        '/api/v1/patients',
        json={
            'name': 'Aarav Patel',
            'date_of_birth': '',
            'phone': '',
            'abha_id': '',
            'preferred_language': 'Hindi'
        }
    )
    log("Patient 1 Status:", r_p1.status_code)
    p1_data = r_p1.json()
    log("Patient 1 ID:", p1_data.get('id'), "abha_id:", repr(p1_data.get('abha_id')))

    # 3. Test Second Patient Registration with another empty abha_id (verify no UNIQUE constraint failure)
    r_p2 = client.post(
        '/api/v1/patients',
        json={
            'name': 'Priya Sharma',
            'date_of_birth': '1995-04-12',
            'phone': '9876543210',
            'abha_id': '  ',
            'preferred_language': 'Tamil'
        }
    )
    log("Patient 2 Status:", r_p2.status_code)
    p2_data = r_p2.json()
    log("Patient 2 ID:", p2_data.get('id'), "abha_id:", repr(p2_data.get('abha_id')))

    # 4. Test Session Creation for Patient 1
    r_s1 = client.post(
        '/api/v1/sessions',
        json={
            'patient_id': p1_data['id'],
            'session_type': 'intake'
        }
    )
    log("Session 1 Status:", r_s1.status_code)
    s1_data = r_s1.json()
    log("Session 1 ID:", s1_data.get('id'), "patient_id:", s1_data.get('patient_id'), "status:", s1_data.get('status'))

    with open('test_output.txt', 'w', encoding='utf-8') as f:
        f.write("\n".join(out_lines))

    assert r_cors.status_code == 200, "Patient creation failed!"
    assert r_cors.headers.get('access-control-allow-origin') in ['*', 'http://localhost:3000'], "CORS header invalid!"
    assert r_p1.status_code == 200, "Patient 1 registration failed!"
    assert r_p2.status_code == 200, "Patient 2 registration failed!"
    assert s1_data.get('patient_id') == p1_data['id'], "Session patient ID mismatch!"
    print("SUCCESS: ALL PATIENT REGISTRATION TESTS PASSED!")

if __name__ == '__main__':
    run_tests()
