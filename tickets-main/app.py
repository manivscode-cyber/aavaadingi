import os
import qrcode
import random
import string
import datetime
import smtplib

from pathlib import Path
from email.message import EmailMessage
from flask import (
    Flask, render_template, render_template_string, request, url_for,
    flash, redirect, send_file
)
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont
from supabase import create_client, Client as SupabaseClient
import razorpay

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv(
    "FLASK_SECRET", "devkey"
)  # required for flash messages

# =========================
# CONFIG
# =========================

BASE_DIR = Path(__file__).resolve().parent
STATIC_TICKETS_DIR = BASE_DIR / "static" / "tickets"
STATIC_TICKETS_DIR.mkdir(parents=True, exist_ok=True)

TEMPLATE_PATH = BASE_DIR / "template.png"
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

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

CATEGORIES = {
    "general": {"name": "General", "price": 500},
    "silver": {"name": "Silver", "price": 1200},
    "gold": {"name": "Gold", "price": 1},
    "platinum": {"name": "Platinum", "price": 5000},
    "vip": {"name": "VIP", "price": 10000},
}

# Temporary memory only for page flow
tickets = {}

# =========================
# VALIDATION
# =========================

required_vars = {
    "SUPABASE_URL": SUPABASE_URL,
    "SUPABASE_KEY": SUPABASE_KEY,
    "PUBLIC_BASE_URL": PUBLIC_BASE_URL,
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


if not TEMPLATE_PATH.exists():
    raise FileNotFoundError(f"template.png not found at: {TEMPLATE_PATH}")

supabase: SupabaseClient = create_client(SUPABASE_URL, SUPABASE_KEY)
razorpay_client = razorpay.Client(
    auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)
)

template = Image.open(TEMPLATE_PATH).convert("RGBA")
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


def get_or_create_ticket(serial: str) -> dict:
    if serial not in tickets:
        tickets[serial] = {
            "category": None,
            "paid": False,
            "buyer_email": None,
            "ticket_image_url": None,
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
):
    payload = {
        "serial": serial,
        "category": category,
        "buyer_email": buyer_email,
        "buyer_whatsapp": buyer_whatsapp or "",
        "quantity": quantity,
        "paid": paid,
        "payment_confirmed_at": (
            datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat()
        ),
        "ticket_image_url": ticket_image_url,
        "email_status": email_status,
        "refunded": refunded,
    }
    return (
        supabase.table("tickets")
        .upsert(payload, on_conflict="serial")
        .execute()
    )


def generate_ticket_image_file(serial: str, category: str) -> Path:
    ticket_image = template.copy()
    draw = ImageDraw.Draw(ticket_image)

    # This QR should point to your validation/check page
    qr_data = f"{PUBLIC_BASE_URL}/ticket/{serial}"
    qr = qrcode.make(qr_data).convert("RGBA")
    qr = qr.resize((250, 250))

    # Adjust positions based on your template
    qr_x, qr_y = 1359, 200
    text_x, text_y = 1359, 500
    category_y = 550

    draw.rectangle(
        [(qr_x, qr_y), (qr_x + 250, qr_y + 250)],
        outline="gold",
        width=5
    )
    ticket_image.paste(qr, (qr_x, qr_y))
    draw.text((text_x, text_y), serial, font=font, fill="gold")
    draw.text(
        (text_x, category_y),
        CATEGORIES[category]["name"],
        font=font,
        fill="gold"
    )

    output_path = STATIC_TICKETS_DIR / f"ticket_{serial}.png"
    ticket_image.save(output_path, format="PNG")
    return output_path


def send_email_with_attachment(
    serial: str, buyer_email: str, image_path: Path
) -> None:
    """Send ticket email with QR code attachment"""
    if not EMAIL_ADDRESS or not EMAIL_PASSWORD:
        raise ValueError("Email credentials not configured in .env")
    
    msg = EmailMessage()
    msg["Subject"] = f"Your AAVADINGI Ticket {serial}"
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = buyer_email
    msg.set_content(
        f"Hello,\n\n"
        f"Thank you for booking AAVADINGI 2026!\n\n"
        f"Your ticket {serial} is attached.\n"
        f"Please keep it safe and show it at entry.\n\n"
        f"See you there!\n\n"
        f"Best regards,\nAVAADINGI Team"
    )

    with open(image_path, "rb") as f:
        image_data = f.read()

    msg.add_attachment(
        image_data,
        maintype="image",
        subtype="png",
        filename=f"ticket_{serial}.png"
    )

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as server:
            server.starttls()
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.send_message(msg)
            app.logger.info("Email sent successfully to %s", buyer_email)
    except smtplib.SMTPAuthenticationError as e:
        raise Exception(
            f"Gmail authentication failed. Check EMAIL_ADDRESS and "
            f"EMAIL_PASSWORD in .env. Error: {e}"
        )
    except smtplib.SMTPException as e:
        raise Exception(f"SMTP error sending email: {e}")
    except Exception as e:
        raise Exception(f"Unexpected error sending email: {e}")


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
    get_or_create_ticket(serial)
    return safe_render_template(
        "ticket.html",
        serial=serial,
        categories=CATEGORIES
    )


@app.route("/pay/<serial>", methods=["GET", "POST"])
def pay_page(serial):
    ticket = tickets.get(serial)

    if request.method == 'GET':
        if (
            not ticket
            or not ticket.get("category")
            or not ticket.get("buyer_email")
            or not ticket.get("quantity")
            or not ticket.get("total_price")
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
        total_price = ticket["total_price"]
    else:  # POST
        category = request.form.get("category", "").strip()
        email = request.form.get("email", "").strip()
        quantity_str = request.form.get("quantity", "1").strip()

        if category not in CATEGORIES:
            flash("Please select a valid ticket category.", "error")
            return redirect(url_for('ticket_page', serial=serial))

        if not email:
            flash("Please enter your email address.", "error")
            return redirect(url_for('ticket_page', serial=serial))

        try:
            quantity = int(quantity_str)
            if quantity < 1 or quantity > 20:
                raise ValueError("Quantity must be between 1 and 20")
        except ValueError:
            flash("Please enter a valid quantity (1-20).", "error")
            return redirect(url_for('ticket_page', serial=serial))

        ticket = get_or_create_ticket(serial)
        ticket["category"] = category
        ticket["buyer_email"] = email
        ticket["quantity"] = quantity

        cat = CATEGORIES[category]
        base_price = cat['price']
        total_price = base_price * quantity

        # Apply bulk discount for 4+ tickets
        if quantity >= 4:
            total_price = int(total_price * 0.85)  # 15% discount

        ticket["total_price"] = total_price

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
                "email": email
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
        result = upsert_ticket_to_supabase(
            serial=serial,
            category=category,
            buyer_email=email,
            quantity=quantity,
            paid=False,
            ticket_image_url="",
            email_status="pending",
            buyer_whatsapp=""
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
        buyer_phone=ticket.get("buyer_phone", "")
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
                result = (
                    supabase.table("tickets")
                    .select("*")
                    .eq("serial", serial)
                    .limit(1)
                    .execute()
                )
                if result.data and len(result.data) > 0:
                    existing = result.data[0]
                    ticket["buyer_email"] = existing.get("buyer_email")
                    ticket["category"] = existing.get("category")
                    ticket["quantity"] = existing.get("quantity", 1)
                    ticket["total_price"] = (
                        existing.get("quantity", 1) *
                        CATEGORIES.get(
                            existing.get("category", "general"), {}
                        ).get("price", 0)
                    )
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
            category = ticket.get("category", "general")
            cat = CATEGORIES.get(category, {})
            return safe_render_template(
                "ticketconfirmation.html",
                serial=serial,
                category_name=cat.get("name", "Unknown"),
                category_key=category,
                price=ticket.get("total_price", 0)
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

        # ===== STEP 5: PROCESS RAZORPAY CALLBACK =====
        # If payment parameters provided, verify and process
        if payment_id and order_id and signature:
            app.logger.info(
                "[CONFIRM] Processing Razorpay callback for %s",
                serial
            )

            # STEP 5.1: Verify signature (return 400 on failure)
            try:
                if not verify_razorpay_signature(order_id, payment_id, signature):
                    app.logger.error(
                        "[CONFIRM] Signature verification failed for %s",
                        serial
                    )
                    return "Payment verification failed. Invalid signature.", 400
            except Exception as e:
                app.logger.error(
                    "[CONFIRM] Signature verification exception: %s - %s",
                    serial, str(e)
                )
                return "Payment verification error.", 400

            # STEP 5.2: Verify payment status with Razorpay
            try:
                payment = razorpay_client.payment.fetch(payment_id)
                if payment.get("status") != "captured":
                    app.logger.warning(
                        "[CONFIRM] Payment not captured for %s. Status: %s",
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
                buyer_whatsapp=""
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
            image_path = generate_ticket_image_file(serial, category)
        except Exception as e:
            app.logger.error(
                "[CONFIRM] Image generation failed: %s - %s",
                serial, str(e)
            )
            return "Failed to generate ticket image", 500

        image_public_url = (
            f"{PUBLIC_BASE_URL}/static/tickets/{image_path.name}"
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
                buyer_whatsapp=""
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
                serial, ticket["buyer_email"], image_path
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
                buyer_whatsapp=""
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
        cat = CATEGORIES.get(category, {})
        final_price = ticket.get(
            "total_price",
            CATEGORIES[category]["price"] * ticket.get("quantity", 1),
        )

        # Handle template name (there's a typo in the filename: ticketconfimation)
        template_name = "ticketconfirmation.html"
        if not (BASE_DIR / "templates" / template_name).exists():
            template_name = "ticketconfimation.html"  # fallback to typo version

        return safe_render_template(
            template_name,
            serial=serial,
            category_name=cat.get("name", "Unknown"),
            category_key=category,
            price=final_price
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
        app.logger.info("[QR] Generating ticket for %s (category: %s)", serial, category)
        
        # Validate category
        if category not in CATEGORIES:
            app.logger.error("[QR] Invalid category: %s", category)
            return "Invalid category", 400
        
        # Generate QR ticket image
        image_path = generate_ticket_image_file(serial, category)
        
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
            result = (
                supabase.table("tickets")
                .select("*")
                .eq("serial", serial)
                .limit(1)
                .execute()
            )
            if not result.data or len(result.data) == 0:
                app.logger.error("[DOWNLOAD] Ticket not found: %s", serial)
                return "Ticket not found", 404
            
            ticket = result.data[0]
            category = ticket.get("category", "general")
            
        except Exception as e:
            app.logger.error("[DOWNLOAD] DB fetch failed: %s - %s", serial, str(e))
            return "Database error", 500
        
        # Validate category
        if category not in CATEGORIES:
            app.logger.error("[DOWNLOAD] Invalid category for %s: %s", serial, category)
            return "Invalid category", 400
        
        # Generate ticket image
        try:
            image_path = generate_ticket_image_file(serial, category)
            app.logger.info("[DOWNLOAD] Generated ticket image: %s", image_path)
        except Exception as e:
            app.logger.error("[DOWNLOAD] Image generation failed: %s - %s", serial, str(e))
            return "Error generating ticket", 500
        
        # Serve as downloadable file
        return send_file(
            image_path,
            mimetype="image/png",
            as_attachment=True,
            download_name=f"AAVADINGI_Ticket_{serial}.png"
        )
        
    except Exception as e:
        app.logger.error("[DOWNLOAD] UNHANDLED ERROR for %s: %s", serial, str(e))
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
        # Test SMTP connection
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as server:
            server.starttls()
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.send_message(
                EmailMessage(
                    subject="AAVADINGI Email Test",
                    from_addr=EMAIL_ADDRESS,
                    to=test_email,
                    text="Email configuration is working!"
                )
            )
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
