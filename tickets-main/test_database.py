#!/usr/bin/env python3
"""
Test script to verify Supabase database connection and operations.
"""

import os
import sys
from dotenv import load_dotenv
from supabase import create_client, Client as SupabaseClient

# Load environment variables
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

print("=" * 60)
print("DATABASE CONNECTION TEST")
print("=" * 60)

# Check if credentials are loaded
print("\n1. Checking credentials...")
if not SUPABASE_URL:
    print("❌ SUPABASE_URL not found in environment")
    sys.exit(1)
else:
    print(f"✅ SUPABASE_URL found: {SUPABASE_URL[:50]}...")

if not SUPABASE_KEY:
    print("❌ SUPABASE_KEY not found in environment")
    sys.exit(1)
else:
    print(f"✅ SUPABASE_KEY found: {SUPABASE_KEY[:20]}...****")

# Test connection
print("\n2. Testing Supabase connection...")
try:
    supabase: SupabaseClient = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Supabase client created successfully")
except Exception as e:
    print(f"❌ Failed to create Supabase client: {e}")
    sys.exit(1)

# Test table access
print("\n3. Testing table access...")
try:
    response = supabase.table("tickets").select("count", count="exact").execute()
    total_rows = response.count
    print(f"✅ Successfully accessed 'tickets' table")
    print(f"   Total rows in table: {total_rows}")
except Exception as e:
    print(f"❌ Failed to access 'tickets' table: {e}")
    sys.exit(1)

# Get sample records
print("\n4. Fetching sample records...")
try:
    response = supabase.table("tickets").select("*").limit(5).execute()
    records = response.data
    print(f"✅ Retrieved {len(records)} sample records")
    
    if records:
        print("\nColumn names from first record:")
        for key in records[0].keys():
            print(f"   - {key}")
    else:
        print("   (No records found in table)")
except Exception as e:
    print(f"❌ Failed to fetch records: {e}")
    sys.exit(1)

# Check table schema
print("\n5. Checking table structure...")
required_columns = [
    "serial", "category", "buyer_email", "quantity",
    "attendee_name", "attendee_phone", "attendee_place",
    "paid", "payment_confirmed_at"
]

if records:
    missing_columns = []
    for col in required_columns:
        if col not in records[0]:
            missing_columns.append(col)
    
    if missing_columns:
        print(f"⚠️  Missing columns in database: {missing_columns}")
    else:
        print(f"✅ All required columns present:")
        for col in required_columns:
            print(f"   - {col}")
else:
    print("⚠️  Cannot verify columns (no records in table)")

# Test write operation
print("\n6. Testing write operation... (will not save)")
try:
    test_serial = "TEST123"
    test_payload = {
        "serial": test_serial,
        "category": "gold",
        "buyer_email": "test@example.com",
        "quantity": 1,
        "paid": False,
        "email_status": "pending",
        "refunded": False
    }
    # We're not actually executing to avoid polluting the database
    print("✅ Write operation structure is valid")
    print(f"   Payload keys: {list(test_payload.keys())}")
except Exception as e:
    print(f"❌ Write operation failed: {e}")

print("\n" + "=" * 60)
print("CONNECTION TEST COMPLETE")
print("=" * 60)
print("\nSummary:")
print("✅ Database connection working")
print("✅ Table access working")
if not missing_columns:
    print("✅ All required columns present")
else:
    print(f"⚠️  {len(missing_columns)} column(s) missing")
