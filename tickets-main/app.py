import os
import qrcode
import random
import string
import datetime
import smtplib
import socket
import time

from pathlib import Path
from email.message import EmailMessage
from email.utils import formataddr
from flask import (
    Flask, render_template, render_template_string, request, url_for,
    flash, redirect, send_file
)
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter
from supabase import create_client, Client as SupabaseClient
import razorpay

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
STATIC_FOLDER = str(BASE_DIR / "static")

app = Flask(__name__, static_folder=STATIC_FOLDER, static_url_path="/static")
app.secret_key = os.getenv(
    "FLASK_SECRET", "devkey"
)  # required for flash messages

# =========================
# CONFIG
# =========================
STATIC_TICKETS_DIR = BASE_DIR / "static" / "tickets"
STATIC_TICKETS_DIR.mkdir(parents=True, exist_ok=True)

LOGO_PATH = BASE_DIR / "static" / "images" / "aavaadingi-logo-wide.png"
TICKET_IMAGE_SIZE = (1600, 900)
RESAMPLE = getattr(Image, "Resampling", Image).LANCZOS
FONT_PATHS = [
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\ARIALUNI.TTF",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
]
FONT_PATH = next((p for p in FONT_PATHS if Path(p).exists()), None)
if not FONT_PATH:
    raise FileNotFoundError(
        "No valid font path found; update FONT_PATH in app.py"
    )

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
EMAIL_FROM_NAME = os.getenv("EMAIL_FROM_NAME", "AAVADINGI Tickets")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587") or "587")
SMTP_USERNAME = os.getenv("SMTP_USERNAME", EMAIL_ADDRESS)
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").strip().lower() not in {
    "0", "false", "no"
}
TEST_PAYMENT_MODE = os.getenv(
    "TEST_PAYMENT_MODE", "true"
).strip().lower() not in {"0", "false", "no"}
TEST_PAYMENT_AMOUNT = max(
    1,
    int(os.getenv("TEST_PAYMENT_AMOUNT", "1") or "1")
)
TEST_PAYMENT_CATEGORY = os.getenv(
    "TEST_PAYMENT_CATEGORY", "gold"
).strip().lower()

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

CATEGORIES = {
    "general": {
        "name": "General",
        "price": 500,
        "perks": "Welcome kit",
        "image": "images/general.jpeg",
        "accent": "#7CC7FF",
    },
    "silver": {
        "name": "Silver",
        "price": 1200,
        "perks": "Welcome kit + Biriyani (Veg or Non-Veg)",
        "image": "images/silver.jpeg",
        "accent": "#D9E0E8",
    },
    "gold": {
        "name": "Gold",
        "price": 2499,
        "perks": (
            "Welcome kit + Biriyani (Veg or Non-Veg) + "
            "T-Shirt + Printed Mug"
        ),
        "image": "images/gold.jpeg",
        "accent": "#FFD36C",
    },
    "platinum": {
        "name": "Platinum",
        "price": 5000,
        "perks": (
            "Welcome kit + Biriyani (Veg or Non-Veg) + "
            "T-Shirt + Printed Mug + PRI Award"
        ),
        "image": "images/platinum.jpeg",
        "accent": "#F4C2FF",
    },
    "vip": {
        "name": "VIP",
        "price": 10000,
        "perks": (
            "Welcome kit + Biriyani (Veg or Non-Veg) + "
            "T-Shirt + Printed Mug + PRI Award + "
            "Vision 2025 Wall of Frame"
        ),
        "image": "images/vip.jpeg",
        "accent": "#FF7F96",
    },
}

# Temporary memory only for page flow
tickets = {}

# =========================
# VALIDATION
# =========================

required_vars = {
    "SUPABASE_URL": SUPABASE_URL,
    "SUPABASE_KEY": SUPABASE_KEY,
    "EMAIL_ADDRESS": EMAIL_ADDRESS,
    "EMAIL_PASSWORD": EMAIL_PASSWORD,
    "RAZORPAY_KEY_ID": RAZORPAY_KEY_ID,
    "RAZORPAY_KEY_SECRET": RAZORPAY_KEY_SECRET,
    # UPI_ID and PAYEE_NAME are now optional since we use Razorpay
    # ADMIN_KEY is optional; do not include here so missing check doesn't fail
}

missing = [key for key, value in required_vars.items() if not value]
if missing:
    raise ValueError(f"Missing environment variables: {', '.join(missing)}")

# simple admin auth check (ADMIN_KEY may be empty string)
ADMIN_KEY = os.getenv("ADMIN_KEY", "")


def check_admin_key(key: str) -> bool:
    # only match if ADMIN_KEY is set
    return ADMIN_KEY != "" and key == ADMIN_KEY


def verify_razorpay_signature(
    order_id: str, payment_id: str, signature: str
) -> bool:
    if not order_id or not payment_id or not signature:
        return False
    try:
        razorpay_client.utility.verify_payment_signature({
            "razorpay_order_id": order_id,
            "razorpay_payment_id": payment_id,
            "razorpay_signature": signature,
        })
        return True
    except Exception as e:
        app.logger.warning("Razorpay signature verification failed: %s", e)
        return False


def fetch_captured_payment(
    payment_id: str,
    retries: int = 5,
    delay_seconds: float = 1.0,
) -> dict:
    last_payment = {}
    for attempt in range(retries):
        last_payment = razorpay_client.payment.fetch(payment_id)
        status = last_payment.get("status")
        if status == "captured":
            return last_payment
        if status not in {"authorized", "created"}:
            return last_payment
        if attempt < retries - 1:
            time.sleep(delay_seconds)
    return last_payment


supabase: SupabaseClient = create_client(SUPABASE_URL, SUPABASE_KEY)
razorpay_client = razorpay.Client(
    auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)
)

font = ImageFont.truetype(FONT_PATH, 40)

# =========================
# HELPERS
# =========================


def safe_render_template(template_name: str, **context):
    template_path = BASE_DIR / "templates" / template_name
    if template_path.exists():
        return render_template(template_name, **context)

    # Fallback - support home.html in repo root while you move it
    root_path = BASE_DIR / template_name
    if root_path.exists():
        return root_path.read_text(encoding="utf-8")

    app.logger.error("Template not found: %s", template_name)
    return (
        f"<h1>Template not found: {template_name}</h1>"
        "<p>Put the file under templates/ and restart.</p>",
        500,
    )


def generate_code(length=6) -> str:
    characters = string.ascii_uppercase + string.digits
    return ''.join(random.choice(characters) for _ in range(length))


def serial_exists_in_supabase(serial: str) -> bool:
    try:
        result = (
            supabase.table("tickets")
            .select("serial")
            .eq("serial", serial)
            .limit(1)
            .execute()
        )
        return bool(result.data)
    except Exception:
        return False


def generate_unique_serial(length=6, max_attempts=100) -> str:
    for _ in range(max_attempts):
        serial = generate_code(length)
        if serial not in tickets and not serial_exists_in_supabase(serial):
            return serial
    raise Exception("Could not generate unique serial after many attempts")


def get_public_base_url() -> str:
    configured = PUBLIC_BASE_URL.rstrip("/")

    # If the configured base URL is local-only, prefer the current request
    # host so generated links work correctly on the deployed site.
    if configured and not any(
        token in configured for token in ("localhost", "127.0.0.1")
    ):
        return configured

    request_root = request.url_root.rstrip("/")
    if request_root:
        return request_root

    return configured


def load_font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_PATH, size)


def compact_text(value: str, fallback: str = "Not provided") -> str:
    cleaned = " ".join((value or "").split())
    return cleaned or fallback


def truncate_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(limit - 3, 1)].rstrip() + "..."


def clean_phone_number(value: str) -> str:
    allowed = {"+", " ", "-", "(", ")"}
    cleaned = "".join(ch for ch in (value or "").strip() if ch.isdigit() or ch in allowed)
    return " ".join(cleaned.split())


def safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def calculate_total_price(category: str, quantity: int) -> int:
    quantity = max(1, quantity)
    if category not in CATEGORIES:
        return 0

    total_price = CATEGORIES[category]["price"] * quantity
    if quantity >= 4:
        total_price = int(total_price * 0.85)

    return total_price


def is_test_payment_active_for_category(category: str) -> bool:
    return TEST_PAYMENT_MODE and category == TEST_PAYMENT_CATEGORY


def calculate_charge_amount(category: str, quantity: int) -> int:
    if is_test_payment_active_for_category(category):
        return TEST_PAYMENT_AMOUNT
    return calculate_total_price(category, quantity)


def fetch_ticket_from_supabase(serial: str) -> dict | None:
    result = (
        supabase.table("tickets")
        .select("*")
        .eq("serial", serial)
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0]
    return None


def sync_ticket_from_record(ticket: dict, record: dict) -> dict:
    category = record.get("category") or ticket.get("category")
    quantity = safe_int(record.get("quantity"), ticket.get("quantity", 1))

    ticket["category"] = category
    ticket["buyer_email"] = record.get("buyer_email") or ticket.get("buyer_email")
    ticket["quantity"] = max(1, quantity)
    ticket["paid"] = bool(record.get("paid", ticket.get("paid", False)))
    ticket["ticket_image_url"] = (
        record.get("ticket_image_url") or ticket.get("ticket_image_url")
    )
    ticket["email_status"] = (
        record.get("email_status") or ticket.get("email_status", "pending")
    )
    ticket["buyer_name"] = (
        record.get("attendee_name")
        or ticket.get("buyer_name", "")
    )
    ticket["buyer_phone"] = (
        record.get("attendee_phone")
        or record.get("buyer_whatsapp")
        or ticket.get("buyer_phone", "")
    )
    ticket["buyer_place"] = (
        record.get("attendee_place")
        or ticket.get("buyer_place", "")
    )
    ticket["buyer_whatsapp"] = (
        record.get("buyer_whatsapp")
        or ticket.get("buyer_phone", "")
    )
    ticket["total_price"] = calculate_charge_amount(category, ticket["quantity"])
    return ticket


def restore_ticket_from_supabase(serial: str, ticket: dict) -> dict | None:
    record = fetch_ticket_from_supabase(serial)
    if not record:
        return None
    sync_ticket_from_record(ticket, record)
    return record


def build_confirmation_context(serial: str, ticket: dict) -> dict:
    category = ticket.get("category", "general")
    cat = CATEGORIES.get(category, {})
    quantity = max(1, safe_int(ticket.get("quantity"), 1))
    test_payment_mode = is_test_payment_active_for_category(category)
    total_price = ticket.get("total_price") or calculate_charge_amount(
        category, quantity
    )
    actual_price = calculate_total_price(category, quantity)

    return {
        "serial": serial,
        "category_key": category,
        "category_name": cat.get("name", "Unknown"),
        "price": total_price,
        "actual_price": actual_price,
        "quantity": quantity,
        "buyer_name": ticket.get("buyer_name", ""),
        "buyer_email": ticket.get("buyer_email", ""),
        "buyer_phone": ticket.get("buyer_phone", ""),
        "buyer_place": ticket.get("buyer_place", ""),
        "ticket_image_url": ticket.get("ticket_image_url", ""),
        "email_status": ticket.get("email_status", "pending"),
        "test_payment_mode": test_payment_mode,
        "test_payment_amount": TEST_PAYMENT_AMOUNT,
    }


def get_or_create_ticket(serial: str) -> dict:
    if serial not in tickets:
        tickets[serial] = {
            "category": None,
            "paid": False,
            "buyer_email": None,
            "ticket_image_url": None,
            "quantity": 1,
            "total_price": 0,
            "buyer_name": "",
            "buyer_phone": "",
            "buyer_place": "",
            "buyer_whatsapp": "",
            "email_status": "pending",
        }
    return tickets[serial]


def upsert_ticket_to_supabase(
    serial: str,
    category: str,
    buyer_email: str,
    quantity: int,
    paid: bool,
    ticket_image_url: str,
    email_status: str,
    refunded: bool = False,
    buyer_whatsapp: str = "",
    attendee_name: str = "",
    attendee_phone: str = "",
    attendee_place: str = "",
):
    payload = {
        "serial": serial,
        "category": category,
        "buyer_email": buyer_email,
        "buyer_whatsapp": buyer_whatsapp or "",
        "attendee_name": attendee_name or "",
        "attendee_phone": attendee_phone or "",
        "attendee_place": attendee_place or "",
        "quantity": quantity,
        "paid": paid,
        "ticket_image_url": ticket_image_url,
        "email_status": email_status,
        "refunded": refunded,
    }
    if paid:
        payload["payment_confirmed_at"] = (
            datetime.datetime.now(datetime.timezone.utc).isoformat()
        )
    return (
        supabase.table("tickets")
        .upsert(payload, on_conflict="serial")
        .execute()
    )


def paste_rounded_image(
    base_image: Image.Image,
    source_image: Image.Image,
    bounds: tuple[int, int, int, int],
    radius: int,
) -> None:
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    prepared = ImageOps.fit(
        source_image.convert("RGBA"),
        (width, height),
        method=RESAMPLE,
    )
    mask = Image.new("L", (width, height), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle(
        [(0, 0), (width, height)],
        radius=radius,
        fill=255,
    )
    base_image.paste(prepared, (bounds[0], bounds[1]), mask)


def draw_detail_row(
    draw: ImageDraw.ImageDraw,
    row_box: tuple[int, int, int, int],
    label: str,
    value: str,
    label_font: ImageFont.FreeTypeFont,
    value_font: ImageFont.FreeTypeFont,
) -> None:
    draw.rounded_rectangle(
        row_box,
        radius=22,
        fill=(255, 255, 255, 240),
        outline=(227, 215, 185, 255),
        width=2,
    )
    draw.text(
        (row_box[0] + 28, row_box[1] + 15),
        label.upper(),
        font=label_font,
        fill=(123, 111, 82, 255),
    )
    draw.text(
        (row_box[0] + 28, row_box[1] + 45),
        value,
        font=value_font,
        fill=(29, 28, 42, 255),
    )


def build_ticket_canvas_background(size: tuple[int, int]) -> Image.Image:
    background = Image.new("RGBA", size, "#0b0a1d")
    draw = ImageDraw.Draw(background)
    top_color = (10, 9, 28)
    bottom_color = (38, 26, 73)

    for y in range(size[1]):
        blend = y / max(size[1] - 1, 1)
        color = tuple(
            int(top_color[idx] + (bottom_color[idx] - top_color[idx]) * blend)
            for idx in range(3)
        )
        draw.line([(0, y), (size[0], y)], fill=color, width=1)

    draw.ellipse((40, 40, 520, 520), fill=(77, 166, 255, 40))
    draw.ellipse((1080, 120, 1580, 620), fill=(255, 215, 0, 35))
    draw.ellipse((1220, 640, 1540, 920), fill=(255, 95, 128, 28))
    return background


def generate_ticket_image_file(
    serial: str, ticket_data: dict, public_base_url: str
) -> Path:
    category = ticket_data.get("category", "general")
    if category not in CATEGORIES:
        raise ValueError(f"Unknown ticket category: {category}")

    category_meta = CATEGORIES[category]
    quantity = max(1, safe_int(ticket_data.get("quantity"), 1))
    test_payment_mode = is_test_payment_active_for_category(category)
    total_price = ticket_data.get("total_price") or calculate_charge_amount(
        category, quantity
    )

    buyer_name = truncate_text(
        compact_text(ticket_data.get("buyer_name"), "Guest"),
        34,
    )
    buyer_email = truncate_text(
        compact_text(ticket_data.get("buyer_email")),
        38,
    )
    buyer_phone = truncate_text(
        compact_text(ticket_data.get("buyer_phone")),
        22,
    )
    buyer_place = truncate_text(
        compact_text(ticket_data.get("buyer_place")),
        26,
    )

    canvas = build_ticket_canvas_background(TICKET_IMAGE_SIZE)
    draw = ImageDraw.Draw(canvas)

    card_bounds = (48, 52, 1552, 848)
    left_bounds = (88, 92, 652, 808)
    right_bounds = (700, 92, 1512, 808)
    qr_box = (1136, 566, 1468, 808)

    draw.rounded_rectangle(
        card_bounds,
        radius=42,
        fill=(14, 14, 30, 236),
        outline=(255, 215, 0, 70),
        width=2,
    )

    category_image_path = BASE_DIR / "static" / category_meta["image"]
    if category_image_path.exists():
        category_art = Image.open(category_image_path).convert("RGBA")
        category_art = category_art.filter(ImageFilter.GaussianBlur(radius=1.5))
        paste_rounded_image(canvas, category_art, left_bounds, radius=34)
    else:
        draw.rounded_rectangle(
            left_bounds,
            radius=34,
            fill=(26, 30, 55, 255),
        )

    draw.rounded_rectangle(
        left_bounds,
        radius=34,
        fill=(7, 10, 23, 96),
        outline=(255, 255, 255, 22),
        width=2,
    )
    draw.rounded_rectangle(
        right_bounds,
        radius=34,
        fill=(248, 243, 232, 252),
        outline=(255, 215, 0, 120),
        width=2,
    )

    accent = category_meta.get("accent", "#FFD700")
    pill_bounds = (124, 130, 410, 190)
    draw.rounded_rectangle(
        pill_bounds,
        radius=28,
        fill=accent,
    )
    draw.text(
        (148, 146),
        f"{category_meta['name'].upper()} PASS",
        font=load_font(30),
        fill=(18, 20, 28, 255),
    )

    draw.text(
        (124, 238),
        "AAVADINGI 2026",
        font=load_font(54),
        fill=(255, 255, 255, 255),
    )
    draw.text(
        (124, 308),
        "Celebrate talent. Celebrate inclusion.",
        font=load_font(26),
        fill=(220, 225, 238, 255),
    )
    draw.text(
        (124, 386),
        f"Ticket Serial  #{serial}",
        font=load_font(34),
        fill=(255, 233, 150, 255),
    )
    draw.text(
        (124, 444),
        f"Quantity  {quantity}",
        font=load_font(28),
        fill=(255, 255, 255, 220),
    )
    amount_label = "Test Charge" if test_payment_mode else "Amount"
    draw.text(
        (124, 490),
        f"{amount_label}  INR {total_price}",
        font=load_font(32),
        fill=(255, 255, 255, 220),
    )
    draw.text(
        (124, 564),
        "Valid for May 23 and 24, 2026",
        font=load_font(26),
        fill=(220, 225, 238, 255),
    )
    draw.text(
        (124, 610),
        "Venue  Chennai",
        font=load_font(26),
        fill=(220, 225, 238, 255),
    )
    draw.multiline_text(
        (124, 686),
        truncate_text(category_meta["perks"], 92),
        font=load_font(24),
        fill=(255, 241, 192, 255),
        spacing=8,
    )

    logo_bg_bounds = (896, 128, 1320, 220)
    draw.rounded_rectangle(
        logo_bg_bounds,
        radius=28,
        fill=(255, 255, 255, 255),
    )
    if LOGO_PATH.exists():
        logo = Image.open(LOGO_PATH).convert("RGBA")
        logo.thumbnail((360, 68), RESAMPLE)
        logo_x = logo_bg_bounds[0] + (
            (logo_bg_bounds[2] - logo_bg_bounds[0] - logo.width) // 2
        )
        logo_y = logo_bg_bounds[1] + (
            (logo_bg_bounds[3] - logo_bg_bounds[1] - logo.height) // 2
        )
        canvas.paste(logo, (logo_x, logo_y), logo)

    draw.text(
        (744, 260),
        "Digital Entry Ticket",
        font=load_font(46),
        fill=(24, 23, 38, 255),
    )
    draw.text(
        (744, 316),
        "Please carry a valid ID along with this QR ticket.",
        font=load_font(22),
        fill=(111, 99, 83, 255),
    )

    detail_layout = [
        ("Name", buyer_name, (744, 372, 1096, 450)),
        ("Contact", buyer_phone, (1116, 372, 1468, 450)),
        ("Email", buyer_email, (744, 470, 1468, 548)),
        ("City / Place", buyer_place, (744, 566, 1096, 644)),
    ]
    for label, value, row_box in detail_layout:
        draw_detail_row(
            draw,
            row_box,
            label,
            value,
            load_font(16),
            load_font(24),
        )

    qr_target = f"{public_base_url}/ticket/{serial}"
    qr = qrcode.make(qr_target).convert("RGBA").resize((248, 248), RESAMPLE)
    draw.rounded_rectangle(
        qr_box,
        radius=26,
        fill=(255, 255, 255, 255),
    )
    qr_x = qr_box[0] + ((qr_box[2] - qr_box[0] - qr.width) // 2)
    qr_y = qr_box[1] + 20
    canvas.paste(qr, (qr_x, qr_y), qr)
    draw.text(
        (qr_box[0] + 42, qr_box[1] + 258),
        "Scan for entry validation",
        font=load_font(18),
        fill=(53, 51, 67, 255),
    )

    draw.text(
        (744, 772),
        truncate_text(qr_target, 56),
        font=load_font(16),
        fill=(123, 111, 82, 255),
    )

    output_path = STATIC_TICKETS_DIR / f"ticket_{serial}.png"
    canvas.convert("RGB").save(output_path, format="PNG")
    return output_path


def send_smtp_message(message: EmailMessage) -> None:
    username = SMTP_USERNAME or EMAIL_ADDRESS

    if not EMAIL_ADDRESS or not EMAIL_PASSWORD:
        raise ValueError("Email credentials not configured in .env")

    try:
        if SMTP_USE_TLS:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(username, EMAIL_PASSWORD)
                server.send_message(message)
        else:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20) as server:
                server.login(username, EMAIL_PASSWORD)
                server.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise RuntimeError(
            "SMTP authentication failed. If you use Gmail, set "
            "EMAIL_PASSWORD to a Gmail App Password."
        ) from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise RuntimeError(
            f"Recipient address was refused by the SMTP server: {exc}"
        ) from exc
    except (TimeoutError, socket.timeout) as exc:
        raise RuntimeError(
            f"SMTP connection to {SMTP_HOST}:{SMTP_PORT} timed out. "
            "Check whether the hosting server can reach the mail server."
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            f"Unable to connect to {SMTP_HOST}:{SMTP_PORT}: {exc}"
        ) from exc
    except smtplib.SMTPException as exc:
        raise RuntimeError(f"SMTP error while sending email: {exc}") from exc


def send_email_with_attachment(
    serial: str,
    ticket_data: dict,
    image_path: Path,
    public_base_url: str,
) -> None:
    buyer_email = ticket_data.get("buyer_email", "")
    if not buyer_email:
        raise ValueError("Buyer email is missing for ticket delivery")

    category = ticket_data.get("category", "general")
    category_name = CATEGORIES.get(category, {}).get("name", "Ticket")
    quantity = max(1, safe_int(ticket_data.get("quantity"), 1))
    download_url = f"{public_base_url}/ticket/download/{serial}"
    buyer_name = compact_text(ticket_data.get("buyer_name"), "Guest")
    buyer_phone = compact_text(ticket_data.get("buyer_phone"))
    buyer_place = compact_text(ticket_data.get("buyer_place"))

    msg = EmailMessage()
    msg["Subject"] = (
        f"Your {category_name} ticket for AAVADINGI 2026 - {serial}"
    )
    msg["From"] = formataddr((EMAIL_FROM_NAME, EMAIL_ADDRESS))
    msg["To"] = buyer_email

    text_body = (
        f"Hello {buyer_name},\n\n"
        f"Your AAVADINGI 2026 ticket is ready.\n\n"
        f"Ticket Type: {category_name}\n"
        f"Ticket Serial: {serial}\n"
        f"Quantity: {quantity}\n"
        f"Email: {buyer_email}\n"
        f"Contact: {buyer_phone}\n"
        f"Place: {buyer_place}\n\n"
        f"Your ticket image is attached to this email.\n"
        f"You can also download it here:\n{download_url}\n\n"
        f"Please keep the ticket safe and show the QR code at the venue.\n\n"
        f"Regards,\nAAVADINGI Team"
    )
    msg.set_content(text_body)

    html_body = f"""
    <html>
      <body style="font-family: Arial, sans-serif; color: #1f1c2c;">
        <div style="max-width: 640px; margin: 0 auto; padding: 24px;">
          <div style="background: linear-gradient(135deg, #0f0c29, #302b63); padding: 24px; border-radius: 20px; color: #ffffff;">
            <h1 style="margin: 0 0 12px; font-size: 28px;">AAVADINGI 2026</h1>
            <p style="margin: 0; font-size: 16px;">Your ticket is ready for entry.</p>
          </div>
          <div style="padding: 24px 0 8px;">
            <p style="font-size: 16px; line-height: 1.6;">Hello {buyer_name},</p>
            <p style="font-size: 16px; line-height: 1.6;">
              Thanks for booking with AAVADINGI. Your <strong>{category_name}</strong>
              ticket is attached to this email.
            </p>
            <table style="width: 100%; border-collapse: collapse; margin: 20px 0;">
              <tr><td style="padding: 10px; border-bottom: 1px solid #ece7db;"><strong>Ticket Serial</strong></td><td style="padding: 10px; border-bottom: 1px solid #ece7db;">{serial}</td></tr>
              <tr><td style="padding: 10px; border-bottom: 1px solid #ece7db;"><strong>Ticket Type</strong></td><td style="padding: 10px; border-bottom: 1px solid #ece7db;">{category_name}</td></tr>
              <tr><td style="padding: 10px; border-bottom: 1px solid #ece7db;"><strong>Quantity</strong></td><td style="padding: 10px; border-bottom: 1px solid #ece7db;">{quantity}</td></tr>
              <tr><td style="padding: 10px; border-bottom: 1px solid #ece7db;"><strong>Email</strong></td><td style="padding: 10px; border-bottom: 1px solid #ece7db;">{buyer_email}</td></tr>
              <tr><td style="padding: 10px; border-bottom: 1px solid #ece7db;"><strong>Contact</strong></td><td style="padding: 10px; border-bottom: 1px solid #ece7db;">{buyer_phone}</td></tr>
              <tr><td style="padding: 10px; border-bottom: 1px solid #ece7db;"><strong>Place</strong></td><td style="padding: 10px; border-bottom: 1px solid #ece7db;">{buyer_place}</td></tr>
            </table>
            <p style="font-size: 16px; line-height: 1.6;">
              You can also download your ticket anytime from
              <a href="{download_url}">{download_url}</a>.
            </p>
            <p style="font-size: 16px; line-height: 1.6;">
              Keep the attached ticket safe and present the QR code at the venue entrance.
            </p>
          </div>
        </div>
      </body>
    </html>
    """
    msg.add_alternative(html_body, subtype="html")

    with open(image_path, "rb") as image_file:
        image_data = image_file.read()

    msg.add_attachment(
        image_data,
        maintype="image",
        subtype="png",
        filename=f"AAVADINGI_Ticket_{serial}.png",
    )

    send_smtp_message(msg)
    app.logger.info("Email sent successfully to %s", buyer_email)


# =========================
# SECURITY HEADERS
# =========================

@app.after_request
def set_security_headers(response):
    """Add security headers to suppress browser warnings"""
    # Permissions-Policy: disable sensor access and privacy concerns
    response.headers['Permissions-Policy'] = (
        'accelerometer=(), '
        'gyroscope=(), '
        'magnetometer=(), '
        'camera=(), '
        'microphone=(), '
        'geolocation=(), '
        'usb=()'
    )
    # Content Security Policy - allow Razorpay and trusted CDNs
    csp_header = (
        "default-src 'self' https:; "
        "script-src 'self' 'unsafe-inline' "
        "https://checkout.razorpay.com "
        "https://cdn.razorpay.com "
        "https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' "
        "https://cdnjs.cloudflare.com "
        "https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com "
        "https://cdnjs.cloudflare.com; "
        "img-src 'self' https: data:; "
        "connect-src 'self' https://api.razorpay.com https://*.razorpay.com; "
    )
    response.headers['Content-Security-Policy'] = csp_header
    # X-Content-Type-Options: prevent MIME type sniffing
    response.headers['X-Content-Type-Options'] = 'nosniff'
    # X-Frame-Options: prevent clickjacking
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    # Referrer-Policy: control referrer information
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response


# =========================
# ROUTES
# =========================

@app.route("/favicon.ico")
def favicon():
    """Suppress favicon 404 errors"""
    return "", 204  # 204 No Content


@app.route("/")
def home():
    return safe_render_template("home.html")


@app.route("/home")
@app.route("/home.html")
def home_explicit():
    """Explicit route for home.html booking page"""
    return safe_render_template("home.html")


@app.route("/index.html")
@app.route("/index")
def landing_page():
    """Serve the Homepage/index.html landing page"""
    try:
        homepage_index = BASE_DIR / "Homepage" / "index.html"
        if homepage_index.exists():
            return homepage_index.read_text(encoding="utf-8")
    except Exception as e:
        app.logger.error("Failed to load Homepage/index.html: %s", e)
    return safe_render_template("home.html")


@app.route("/start")
def start_ticket():
    serial = generate_unique_serial()
    return safe_render_template("start.html", serial=serial)


@app.route("/ticket/<serial>")
def ticket_page(serial):
    ticket = get_or_create_ticket(serial)
    if not ticket.get("category") or not ticket.get("buyer_email"):
        try:
            restore_ticket_from_supabase(serial, ticket)
        except Exception as exc:
            app.logger.warning(
                "[TICKET] Could not restore %s from Supabase: %s",
                serial,
                exc,
            )
    return safe_render_template(
        "ticket.html",
        serial=serial,
        categories=CATEGORIES,
        ticket=ticket,
        selected_category=ticket.get("category", ""),
        selected_quantity=max(1, safe_int(ticket.get("quantity"), 1)),
    )


@app.route("/pay/<serial>", methods=["GET", "POST"])
def pay_page(serial):
    ticket = get_or_create_ticket(serial)

    if request.method == 'GET':
        required_fields = [
            ticket.get("category"),
            ticket.get("buyer_email"),
            ticket.get("buyer_name"),
            ticket.get("buyer_phone"),
            ticket.get("buyer_place"),
            ticket.get("quantity"),
        ]
        if not all(required_fields):
            try:
                restore_ticket_from_supabase(serial, ticket)
            except Exception as exc:
                app.logger.warning(
                    "[PAY] Could not restore %s from Supabase: %s",
                    serial,
                    exc,
                )

        if (
            not ticket.get("category")
            or not ticket.get("buyer_email")
            or not ticket.get("buyer_name")
            or not ticket.get("buyer_phone")
            or not ticket.get("buyer_place")
            or not ticket.get("quantity")
        ):
            flash(
                "Session expired or incomplete ticket data. "
                "Please start again.",
                "error"
            )
            return redirect(url_for('ticket_page', serial=serial))

        category = ticket["category"]
        email = ticket["buyer_email"]
        quantity = ticket["quantity"]
        total_price = ticket.get("total_price") or calculate_charge_amount(
            category,
            quantity,
        )
        ticket["total_price"] = total_price
    else:  # POST
        category = request.form.get("category", "").strip()
        email = request.form.get("email", "").strip()
        buyer_name = compact_text(request.form.get("name", ""), "")
        buyer_phone = clean_phone_number(request.form.get("phone", ""))
        buyer_place = compact_text(request.form.get("place", ""), "")
        quantity_str = request.form.get("quantity", "1").strip()

        # Older cached ticket pages only submit email/category. Guide users to
        # refresh so they load the current booking form with all required fields.
        if (
            email
            and not request.form.get("name")
            and not request.form.get("phone")
            and not request.form.get("place")
        ):
            flash(
                "This booking page is outdated. Please refresh once and enter "
                "your full contact details.",
                "error"
            )
            return redirect(url_for('ticket_page', serial=serial))

        if category not in CATEGORIES:
            flash("Please select a valid ticket category.", "error")
            return redirect(url_for('ticket_page', serial=serial))

        if not buyer_name:
            flash("Please enter your full name.", "error")
            return redirect(url_for('ticket_page', serial=serial))

        if not email:
            flash("Please enter your email address.", "error")
            return redirect(url_for('ticket_page', serial=serial))

        phone_digits = "".join(ch for ch in buyer_phone if ch.isdigit())
        if len(phone_digits) < 10:
            flash("Please enter a valid contact number.", "error")
            return redirect(url_for('ticket_page', serial=serial))

        if not buyer_place:
            flash("Please enter your city or place.", "error")
            return redirect(url_for('ticket_page', serial=serial))

        try:
            quantity = int(quantity_str)
            if quantity < 1 or quantity > 20:
                raise ValueError("Quantity must be between 1 and 20")
        except ValueError:
            flash("Please enter a valid quantity (1-20).", "error")
            return redirect(url_for('ticket_page', serial=serial))

        ticket["category"] = category
        ticket["buyer_email"] = email
        ticket["quantity"] = quantity
        ticket["buyer_name"] = buyer_name
        ticket["buyer_phone"] = buyer_phone
        ticket["buyer_place"] = buyer_place
        ticket["buyer_whatsapp"] = buyer_phone

        total_price = calculate_charge_amount(category, quantity)
        ticket["total_price"] = total_price

    actual_price = calculate_total_price(category, quantity)
    test_payment_mode = is_test_payment_active_for_category(category)

    # Create Razorpay order (new order for refresh)
    try:
        now = datetime.datetime.now()
        timestamp = now.strftime('%Y%m%d%H%M%S')
        receipt_id = f"AAVAA-{serial}-{timestamp}"
        razorpay_order = razorpay_client.order.create({
            "amount": int(total_price * 100),
            "currency": "INR",
            "receipt": receipt_id,
            "payment_capture": 1,
            "notes": {
                "serial": serial,
                "category": category,
                "quantity": quantity,
                "email": email,
                "name": ticket.get("buyer_name", ""),
                "phone": ticket.get("buyer_phone", ""),
                "test_payment_mode": str(test_payment_mode).lower(),
            }
        })
    except Exception as e:
        app.logger.exception("Razorpay order.create failed: %s", e)
        flash(
            "Payment gateway unavailable. Please try again "
            "in a few minutes.",
            "error"
        )
        return redirect(url_for("ticket_page", serial=serial))

    # Save ticket data to Supabase early for recovery after payment
    try:
        app.logger.info(
            "Saving ticket to Supabase: serial=%s, email=%s, "
            "category=%s, quantity=%s",
            serial, email, category, quantity
        )
        upsert_ticket_to_supabase(
            serial=serial,
            category=category,
            buyer_email=email,
            quantity=quantity,
            paid=False,
            ticket_image_url="",
            email_status="pending",
            buyer_whatsapp=ticket.get("buyer_phone", ""),
            attendee_name=ticket.get("buyer_name", ""),
            attendee_phone=ticket.get("buyer_phone", ""),
            attendee_place=ticket.get("buyer_place", ""),
        )
        app.logger.info(
            "Successfully saved ticket to Supabase for %s",
            serial
        )
    except Exception as e:
        app.logger.error(
            "Failed to save ticket to Supabase for %s: %s",
            serial,
            str(e)
        )

    confirm_payment_callback_url = (
        f"{get_public_base_url()}{url_for('confirm_payment', serial=serial)}"
    )

    # render the payment template
    return safe_render_template(
        "pay.html",
        serial=serial,
        category_key=category,
        category_name=CATEGORIES[category]["name"],
        price=total_price,
        quantity=quantity,
        razorpay_order_id=razorpay_order["id"],
        razorpay_key_id=RAZORPAY_KEY_ID,
        buyer_name=ticket.get("buyer_name", ""),
        buyer_email=email,
        buyer_phone=ticket.get("buyer_phone", ""),
        buyer_place=ticket.get("buyer_place", ""),
        actual_price=actual_price,
        test_payment_mode=test_payment_mode,
        test_payment_amount=TEST_PAYMENT_AMOUNT,
        confirm_payment_callback_url=confirm_payment_callback_url,
    )


@app.route("/confirm/<serial>", methods=["GET", "POST"])
def confirm_payment(serial):
    """
    Production-grade payment confirmation handler with comprehensive
    error handling, idempotency, and logging.

    Requirements handled:
    1. request.args.get() for safe parameter extraction (no KeyError)
    2. HTTP 400 for missing/invalid parameters
    3. Idempotency: already-paid tickets return success without reprocessing
    4. Comprehensive logging for debugging production issues
    5. Safe database queries with 404 handling
    6. Try-except wrapping entire logic to prevent crashes
    7. Proper HTTP status codes (400, 404, 500)
    """
    try:
        # ===== STEP 1: RESTORE TICKET FROM CACHE OR DATABASE =====
        ticket = get_or_create_ticket(serial)

        # If buyer_email/category missing, fetch from Supabase
        if not ticket.get("buyer_email") or not ticket.get("category"):
            app.logger.info(
                "[CONFIRM] Ticket data missing in memory for %s, "
                "fetching from Supabase",
                serial
            )
            try:
                existing = restore_ticket_from_supabase(serial, ticket)
                if existing:
                    app.logger.info(
                        "[CONFIRM] Restored ticket %s from Supabase: "
                        "email=%s, category=%s, paid=%s",
                        serial,
                        existing.get("buyer_email"),
                        existing.get("category"),
                        existing.get("paid")
                    )
                else:
                    app.logger.error(
                        "[CONFIRM] Ticket not found in Supabase: %s",
                        serial
                    )
                    return "Ticket not found", 404
            except Exception as e:
                app.logger.error(
                    "[CONFIRM] Failed to fetch from Supabase: %s - %s",
                    serial, str(e)
                )
                return "Database error", 500

        # ===== STEP 2: VALIDATE TICKET DATA EXISTS =====
        if not ticket.get("buyer_email") or not ticket.get("category"):
            app.logger.error(
                "[CONFIRM] Missing buyer details for %s: "
                "email=%s, category=%s",
                serial,
                ticket.get("buyer_email"),
                ticket.get("category")
            )
            return "Ticket information incomplete. Start again.", 400

        # ===== STEP 3: CHECK IF ALREADY PAID (IDEMPOTENCY) =====
        # If already paid, return success without reprocessing
        if ticket.get("paid"):
            app.logger.info(
                "[CONFIRM] Ticket %s already marked as paid. "
                "Returning success (idempotent).",
                serial
            )
            return safe_render_template(
                "ticketconfirmation.html",
                **build_confirmation_context(serial, ticket)
            )

        # ===== STEP 4: EXTRACT PAYMENT PARAMETERS SAFELY =====
        # Use request.args.get() and request.form.get() to avoid KeyError
        payment_id = (
            request.args.get("payment_id")
            or request.args.get("razorpay_payment_id")
            or request.form.get("payment_id")
            or request.form.get("razorpay_payment_id")
        )

        order_id = (
            request.args.get("order_id")
            or request.args.get("razorpay_order_id")
            or request.form.get("order_id")
            or request.form.get("razorpay_order_id")
        )

        signature = (
            request.args.get("signature")
            or request.args.get("razorpay_signature")
            or request.form.get("signature")
            or request.form.get("razorpay_signature")
        )

        if not (payment_id and order_id and signature):
            app.logger.error(
                "[CONFIRM] Missing Razorpay callback parameters for unpaid "
                "ticket %s",
                serial
            )
            return "Missing payment confirmation parameters.", 400

        # ===== STEP 5: PROCESS RAZORPAY CALLBACK =====
        # If payment parameters provided, verify and process
        app.logger.info(
            "[CONFIRM] Processing Razorpay callback for %s",
            serial
        )

        # STEP 5.1: Verify signature (return 400 on failure)
        try:
            sig_valid = verify_razorpay_signature(
                order_id, payment_id, signature
            )
            if not sig_valid:
                app.logger.error(
                    "[CONFIRM] Signature verification failed for %s",
                    serial
                )
                msg = "Payment verification failed. Invalid "
                msg += "signature."
                return msg, 400
        except Exception as e:
            app.logger.error(
                "[CONFIRM] Signature verification exception: "
                "%s - %s",
                serial, str(e)
            )
            return "Payment verification error.", 400

        # STEP 5.2: Verify payment status with Razorpay
        try:
            payment = fetch_captured_payment(payment_id)
            if payment.get("status") != "captured":
                app.logger.warning(
                    "[CONFIRM] Payment not captured for %s. "
                    "Status: %s",
                    serial, payment.get("status")
                )
                return (
                    "Payment not completed. Status: " +
                    payment.get("status", "unknown"),
                    400
                )
            app.logger.info(
                "[CONFIRM] Payment verified captured for %s",
                serial
            )
        except Exception as e:
            app.logger.error(
                "[CONFIRM] Payment fetch failed: %s - %s",
                serial, str(e)
            )
            return "Payment verification failed.", 400

        # ===== STEP 6: VALIDATE CATEGORY =====
        category = ticket.get("category")

        if category not in CATEGORIES:
            app.logger.error(
                "[CONFIRM] Invalid category for %s: %s",
                serial, category
            )
            return "Invalid ticket category", 400

        # ===== STEP 7: MARK TICKET AS PAID =====
        ticket["paid"] = True

        # ===== STEP 8: PROCESS TICKET DELIVERY =====

        # 8.1: Save to Supabase
        try:
            app.logger.info(
                "[CONFIRM] Saving ticket to Supabase: %s",
                serial
            )
            upsert_ticket_to_supabase(
                serial=serial,
                category=category,
                buyer_email=ticket["buyer_email"],
                quantity=ticket.get("quantity", 1),
                paid=True,
                ticket_image_url="",
                email_status="pending",
                buyer_whatsapp=ticket.get("buyer_phone", ""),
                attendee_name=ticket.get("buyer_name", ""),
                attendee_phone=ticket.get("buyer_phone", ""),
                attendee_place=ticket.get("buyer_place", ""),
            )
        except Exception as e:
            app.logger.error(
                "[CONFIRM] Failed to save to Supabase: %s - %s",
                serial, str(e)
            )
            return "Failed to save ticket to database", 500

        # 8.2: Generate QR code ticket image
        try:
            app.logger.info(
                "[CONFIRM] Generating ticket image for %s",
                serial
            )
            public_base_url = get_public_base_url()
            image_path = generate_ticket_image_file(
                serial,
                ticket,
                public_base_url,
            )
        except Exception as e:
            app.logger.error(
                "[CONFIRM] Image generation failed: %s - %s",
                serial, str(e)
            )
            return "Failed to generate ticket image", 500

        image_public_url = (
            f"{public_base_url}/static/tickets/{image_path.name}"
        )
        ticket["ticket_image_url"] = image_public_url

        # 8.3: Update image URL in database
        try:
            upsert_ticket_to_supabase(
                serial=serial,
                category=category,
                buyer_email=ticket["buyer_email"],
                quantity=ticket.get("quantity", 1),
                paid=True,
                ticket_image_url=image_public_url,
                email_status="pending",
                buyer_whatsapp=ticket.get("buyer_phone", ""),
                attendee_name=ticket.get("buyer_name", ""),
                attendee_phone=ticket.get("buyer_phone", ""),
                attendee_place=ticket.get("buyer_place", ""),
            )
        except Exception as e:
            app.logger.error(
                "[CONFIRM] Failed to update image URL: %s - %s",
                serial, str(e)
            )
            # Don't fail entire flow, continue to email

        # 8.4: Send email with ticket attachment
        email_status = "pending"
        try:
            app.logger.info(
                "[CONFIRM] Sending email to %s for ticket %s",
                ticket["buyer_email"], serial
            )
            send_email_with_attachment(
                serial,
                ticket,
                image_path,
                public_base_url,
            )
            email_status = "sent"
            app.logger.info(
                "[CONFIRM] Email sent successfully for %s",
                serial
            )
        except Exception as e:
            app.logger.error(
                "[CONFIRM] Email sending failed: %s - %s",
                serial, str(e)
            )
            # Still mark ticket as processed
            email_status = f"failed: {str(e)}"
        ticket["email_status"] = email_status

        # 8.5: Final status update
        try:
            upsert_ticket_to_supabase(
                serial=serial,
                category=category,
                buyer_email=ticket["buyer_email"],
                quantity=ticket.get("quantity", 1),
                paid=True,
                ticket_image_url=image_public_url,
                email_status=email_status,
                buyer_whatsapp=ticket.get("buyer_phone", ""),
                attendee_name=ticket.get("buyer_name", ""),
                attendee_phone=ticket.get("buyer_phone", ""),
                attendee_place=ticket.get("buyer_place", ""),
            )
        except Exception as e:
            app.logger.error(
                "[CONFIRM] Final status update failed: %s - %s",
                serial, str(e)
            )
            # Don't fail if email already sent

        app.logger.info(
            "[CONFIRM] Ticket %s processed. Email status: %s",
            serial, email_status
        )

        # ===== STEP 9: RENDER CONFIRMATION PAGE =====
        return safe_render_template(
            "ticketconfirmation.html",
            **build_confirmation_context(serial, ticket)
        )

    except Exception as e:
        # CATCH-ALL: Log any unhandled exception
        app.logger.exception(
            "[CONFIRM] UNHANDLED EXCEPTION for %s: %s",
            serial, str(e)
        )
        # Render error page safely without needing 'cat' variable
        try:
            return safe_render_template(
                "error.html",
                error="Payment confirmation failed. Please contact support.",
                serial=serial
            )
        except Exception:
            # Final fallback if error.html doesn't exist
            return "Internal server error. Check logs.", 500


@app.route("/qr/<serial>/<category>")
def get_qr_ticket(serial: str, category: str):
    """Generate or retrieve QR code ticket image"""
    try:
        app.logger.info(
            "[QR] Generating ticket for %s (category: %s)",
            serial, category
        )

        # Validate category
        if category not in CATEGORIES:
            app.logger.error("[QR] Invalid category: %s", category)
            return "Invalid category", 400

        ticket = get_or_create_ticket(serial)
        if not ticket.get("category"):
            try:
                restore_ticket_from_supabase(serial, ticket)
            except Exception as exc:
                app.logger.warning(
                    "[QR] Could not restore %s from Supabase: %s",
                    serial,
                    exc,
                )

        if ticket.get("category") not in CATEGORIES:
            ticket["category"] = category

        public_base_url = get_public_base_url()
        image_path = generate_ticket_image_file(
            serial,
            ticket,
            public_base_url,
        )

        # Serve the image
        app.logger.info("[QR] Serving ticket image: %s", image_path)
        return send_file(
            image_path,
            mimetype="image/png",
            as_attachment=False,
            download_name=f"ticket_{serial}.png"
        )

    except Exception as e:
        app.logger.error("[QR] Error generating QR: %s - %s", serial, str(e))
        return "Error generating QR code", 500


@app.route("/ticket/download/<serial>")
def download_ticket(serial: str):
    """Download ticket as PNG file"""
    try:
        app.logger.info("[DOWNLOAD] Ticket download requested for %s", serial)

        # Fetch ticket from Supabase to get category
        try:
            record = fetch_ticket_from_supabase(serial)
            if not record:
                app.logger.error("[DOWNLOAD] Ticket not found: %s", serial)
                return "Ticket not found", 404

            ticket = get_or_create_ticket(serial)
            sync_ticket_from_record(ticket, record)
            category = ticket.get("category", "general")

        except Exception as e:
            app.logger.error(
                "[DOWNLOAD] DB fetch failed: %s - %s",
                serial, str(e)
            )
            return "Database error", 500

        # Validate category
        if category not in CATEGORIES:
            app.logger.error(
                "[DOWNLOAD] Invalid category for %s: %s",
                serial, category
            )
            return "Invalid category", 400

        # Generate ticket image
        try:
            public_base_url = get_public_base_url()
            image_path = generate_ticket_image_file(
                serial,
                ticket,
                public_base_url,
            )
            msg = "[DOWNLOAD] Generated ticket image: %s"
            app.logger.info(msg, image_path)
        except Exception as e:
            app.logger.error(
                "[DOWNLOAD] Image generation failed: %s - %s",
                serial, str(e)
            )
            return "Error generating ticket", 500

        # Serve as downloadable file
        return send_file(
            image_path,
            mimetype="image/png",
            as_attachment=True,
            download_name=(
                f"AAVADINGI_{CATEGORIES[category]['name']}_{serial}.png"
            ),
        )

    except Exception as e:
        app.logger.error(
            "[DOWNLOAD] UNHANDLED ERROR for %s: %s",
            serial, str(e)
        )
        return "Error downloading ticket", 500


# --- admin interface --------------------------------------------------------
@app.route("/admin")
def admin_panel():
    key = request.args.get("key", "")
    if not check_admin_key(key):
        return "Forbidden", 403

    try:
        resp = supabase.table("tickets").select("*").execute()
        rows = resp.data or []
    except Exception as e:
        return f"Failed to fetch tickets: {e}", 500

    return render_template("admin.html", tickets=rows)


@app.route("/admin/refund/<serial>", methods=["POST"])
def admin_refund(serial):
    key = request.args.get("key", "")
    if not check_admin_key(key):
        return "Forbidden", 403
    try:
        (
            supabase.table("tickets")
            .update({"refunded": True})
            .eq("serial", serial)
            .execute()
        )
    except Exception as e:
        return f"Failed to mark refund: {e}", 500
    return ("", 204)


@app.route("/privacy-policy")
def privacy_policy():
    return render_template_string("""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Privacy Policy - AAVADINGI 2026</title>
        <style>
            body {
                font-family: 'Poppins', sans-serif;
                background: linear-gradient(135deg, #0f0c29,
                    #302b63, #24243e);
                color: #fff;
                margin: 0;
                padding: 2rem;
                max-width: 800px;
                margin: 0 auto;
            }
            h1 { color: #ffd700; text-align: center;
                margin-bottom: 2rem; }
            h2 { color: #ffea80; margin-top: 2rem; }
            p, li { line-height: 1.6; }
            a { color: #ffd700; text-decoration: none; }
            a:hover { text-decoration: underline; }
            .back-link {
                display: inline-block;
                margin-top: 2rem;
                padding: 1rem 2rem;
                background: rgba(255,215,0,0.1);
                border: 1px solid #ffd700;
                border-radius: 8px;
                color: #ffd700;
                text-decoration: none;
            }
            .back-link:hover {
                background: rgba(255,215,0,0.2);
            }
        </style>
    </head>
    <body>
        <h1>Privacy Policy</h1>
        <p><strong>Effective Date:</strong> January 1, 2026</p>

        <h2>Information We Collect</h2>
        <p>We collect information you provide directly to us,
        such as when you purchase tickets: your email
        address.</p>

        <h2>How We Use Your Information</h2>
        <ul>
            <li>To send your digital ticket and event updates</li>
            <li>To communicate important event information</li>
            <li>To provide customer support</li>
        </ul>

        <h2>Information Sharing</h2>
        <p>We do not sell, trade, or otherwise transfer your
        personal information to third parties without your
        consent, except as described in this policy.</p>

        <h2>Data Security</h2>
        <p>We implement appropriate security measures to protect
        your personal information against unauthorized access,
        alteration, disclosure, or destruction.</p>

        <h2>Contact Us</h2>
        <p>If you have questions about this Privacy Policy,
        please contact us at
        <a href="mailto:privacy@aavadingi.com">
        privacy@aavadingi.com</a></p>

        <a href="/" class="back-link">← Back to Home</a>
    </body>
    </html>
    """)


@app.route("/terms-and-conditions")
def terms_and_conditions():
    return render_template_string("""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Terms & Conditions - AAVADINGI 2026</title>
        <style>
            body {
                font-family: 'Poppins', sans-serif;
                background: linear-gradient(135deg, #0f0c29,
                    #302b63, #24243e);
                color: #fff;
                margin: 0;
                padding: 2rem;
                max-width: 800px;
                margin: 0 auto;
            }
            h1 {
                color: #ffd700;
                text-align: center;
                margin-bottom: 2rem;
            }
            h2 { color: #ffea80; margin-top: 2rem; }
            p, li { line-height: 1.6; }
            a { color: #ffd700; text-decoration: none; }
            a:hover { text-decoration: underline; }
            .back-link {
                display: inline-block;
                margin-top: 2rem;
                padding: 1rem 2rem;
                background: rgba(255,215,0,0.1);
                border: 1px solid #ffd700;
                border-radius: 8px;
                color: #ffd700;
                text-decoration: none;
            }
            .back-link:hover { background: rgba(255,215,0,0.2); }
        </style>
    </head>
    <body>
        <h1>Terms & Conditions</h1>
        <p><strong>Effective Date:</strong> January 1, 2026</p>

        <h2>Ticket Purchase</h2>
        <p>By purchasing a ticket to AAVADINGI 2026, you
        agree to these terms and conditions.</p>

        <h2>Refund Policy</h2>
        <p>All ticket sales are final. No refunds will be
        provided except in cases of event cancellation by the
        organizers.</p>

        <h2>Event Changes</h2>
        <p>We reserve the right to make changes to the event
        schedule, venue, or program. Ticket holders will be
        notified of any significant changes.</p>

        <h2>Entry Requirements</h2>
        <ul>
            <li>Valid ticket (digital or printed) is required for entry</li>
            <li>Photo ID may be required</li>
            <li>Venue rules and regulations must be followed</li>
        </ul>

        <h2>Liability</h2>
        <p>AAVADINGI organizers are not liable for any loss,
        damage, or injury occurring at the event.</p>

        <h2>Contact Information</h2>
        <p>For questions about these terms, please contact us
        at <a href="mailto:info@aavadingi.com">
        info@aavadingi.com</a></p>

        <a href="/" class="back-link">← Back to Home</a>
    </body>
    </html>
    """)


@app.route("/static/homepage-styles.css")
def homepage_styles():
    """Serve Homepage/styles.css"""
    try:
        styles_path = BASE_DIR / "Homepage" / "styles.css"
        if styles_path.exists():
            content = styles_path.read_text(encoding="utf-8")
            return content, 200, {'Content-Type': 'text/css'}
    except Exception as e:
        app.logger.error("Failed to load Homepage styles: %s", e)
    return "", 404


@app.route("/images/<path:filename>")
def homepage_images(filename):
    """Serve images from Homepage/images folder"""
    try:
        image_path = BASE_DIR / "Homepage" / "images" / filename
        if image_path.exists():
            return send_file(image_path)
    except Exception as e:
        app.logger.error("Failed to load image: %s", e)
    return "", 404


@app.errorhandler(500)
def internal_server_error(err):
    app.logger.exception("Unhandled 500")
    return "Internal server error. See logs.", 500


@app.route("/health/email-test", methods=["POST"])
def email_test():
    """Test email configuration - Admin only"""
    key = request.form.get("key", "")
    if not check_admin_key(key):
        return "Unauthorized", 401

    test_email = request.form.get("email", "test@example.com")

    try:
        msg = EmailMessage()
        msg["Subject"] = "AAVADINGI Email Test"
        msg["From"] = formataddr((EMAIL_FROM_NAME, EMAIL_ADDRESS))
        msg["To"] = test_email
        msg.set_content("Email configuration is working.")
        send_smtp_message(msg)
        return (
            f"✅ Email test successful! Sent to {test_email}"
        ), 200
    except smtplib.SMTPAuthenticationError as e:
        return (
            f"❌ Gmail authentication failed. Check EMAIL_ADDRESS "
            f"and EMAIL_PASSWORD. Error: {e}"
        ), 400
    except Exception as e:
        return f"❌ Email test failed: {e}", 400


if __name__ == "__main__":
    app.run(port=5002)
