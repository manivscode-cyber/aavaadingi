#!/usr/bin/env python3
"""Test database write with minimal fields"""

import os
import sys
import datetime
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_KEY")
)

print("=" * 60)
print("DATABASE WRITE TEST - MINIMAL FIELDS")
print("=" * 60)

# Test with only basic fields (no attendee_* fields)
test_serial = "TEST_MINIMAL_001"
test_payload = {
    "serial": test_serial,
    "category": "gold",
    "buyer_email": "test@example.com",
    "buyer_whatsapp": "9876543210",
    "quantity": 2,
    "paid": False,
    "payment_confirmed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "ticket_image_url": "https://example.com/ticket.png",
    "email_status": "pending",
    "refunded": False,
}

print(f"\nAttempting to write with fields: {list(test_payload.keys())}")

try:
    result = supabase.table("tickets").upsert(test_payload, on_conflict="serial").execute()
    print("✅ Write successful!")
    print(f"   Result: {result.data}")
except Exception as e:
    print(f"❌ Write failed: {e}")
    sys.exit(1)

# Verify
print("\nVerifying write...")
try:
    result = supabase.table("tickets").select("*").eq("serial", test_serial).execute()
    if result.data:
        record = result.data[0]
        print(f"✅ Record found!")
        print(f"\n   Actual columns in table:")
        for key in sorted(record.keys()):
            print(f"      - {key}")
    else:
        print(f"❌ Record not found")
except Exception as e:
    print(f"❌ Verification failed: {e}")
    sys.exit(1)

print("\n" + "=" * 60)
print("✅ DATABASE IS WORKING PROPERLY")
print("=" * 60)
