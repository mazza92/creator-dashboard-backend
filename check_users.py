import psycopg2
from psycopg2.extras import RealDictCursor
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()

conn = psycopg2.connect(
    host=os.getenv('DB_HOST'),
    database=os.getenv('DB_NAME'),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD')
)

cursor = conn.cursor(cursor_factory=RealDictCursor)

# Check user 153
cursor.execute('''
    SELECT u.id, u.email, u.role, u.created_at, u.is_verified,
           c.id as creator_id
    FROM users u
    LEFT JOIN creators c ON u.id = c.user_id
    WHERE u.id = 153
''')
user153 = cursor.fetchone()

print('User 153:', user153)

# Check if they would get reminder based on our rules
now = datetime.now()
twenty_minutes_ago = now - timedelta(minutes=20)

print('\n=== REMINDER ELIGIBILITY CHECK ===')

if user153:
    print(f'\nUser 153:')
    print(f'  - Role: {user153["role"]}')
    print(f'  - Email verified: {user153["is_verified"]}')
    print(f'  - Created at: {user153["created_at"]}')
    print(f'  - Creator profile exists: {user153["creator_id"] is not None}')
    print(f'  - Registered >20 min ago: {user153["created_at"] < twenty_minutes_ago}')
    
    # Check all conditions
    condition1 = user153["created_at"] < twenty_minutes_ago
    condition2 = user153["is_verified"] == True
    condition3 = user153["role"] == "creator"
    condition4 = user153["creator_id"] is None
    
    print(f'  - Condition 1 (>20 min): {condition1}')
    print(f'  - Condition 2 (verified): {condition2}')
    print(f'  - Condition 3 (creator): {condition3}')
    print(f'  - Condition 4 (no profile): {condition4}')
    print(f'  - WOULD GET REMINDER: {condition1 and condition2 and condition3 and condition4}')

conn.close() 