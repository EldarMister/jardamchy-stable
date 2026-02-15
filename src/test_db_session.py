from db import get_db
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_session_flow():
    db = get_db()
    user_id = "test_user_999"
    
    print(f"--- Testing Session Flow for {user_id} ---")
    
    # 1. Create session (mimics /register)
    print("1. Creating session...")
    db.create_telegram_session(user_id)
    session = db.get_telegram_session(user_id)
    print(f"Session after create: {session.get('temp_data')}")
    
    # 2. Set driver type (mimics button click)
    print("2. Setting driver_type='taxi'...")
    db.set_telegram_session_data(user_id, 'driver_type', 'taxi')
    session = db.get_telegram_session(user_id)
    print(f"Session after type: {session.get('temp_data')}")
    
    # 3. Set name (mimics text input)
    print("3. Setting name='Ivanchik'...")
    db.set_telegram_session_data(user_id, 'name', 'Ivanchik')
    session = db.get_telegram_session(user_id)
    print(f"Session after name: {session.get('temp_data')}")
    
    # 4. Set phone
    print("4. Setting phone='0555112233'...")
    db.set_telegram_session_data(user_id, 'phone', '0555112233')
    session = db.get_telegram_session(user_id)
    print(f"Session after phone: {session.get('temp_data')}")

    # 5. Check persistence
    print("5. Double checking final state...")
    final_data = session.get('temp_data', {})
    if final_data.get('name') == 'Ivanchik' and final_data.get('phone') == '0555112233':
        print("SUCCESS: Data persisted correctly.")
    else:
        print("FAILURE: Data lost!")

if __name__ == "__main__":
    try:
        test_session_flow()
    except Exception as e:
        print(f"CRASH: {e}")
