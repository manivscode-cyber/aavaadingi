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
    "/Library/Fonts/Arial Unicode.ttf"
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
):
    payload = {
        "serial": serial,
        "category": category,
        "buyer_email": buyer_email,
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
    msg = EmailMessage()
    msg["Subject"] = f"Your Ticket {serial}"
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = buyer_email
    msg.set_content(
        f"Hello,\n\nYour ticket {serial} is attached.\n"
        f"Please keep it safe and show it at entry.\n\nThank you."
    )

    with open(image_path, "rb") as f:
        image_data = f.read()

    msg.add_attachment(
        image_data,
        maintype="image",
        subtype="png",
        filename=f"ticket_{serial}.png"
    )

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        server.send_message(msg)


# =========================
# SECURITY HEADERS
# =========================

@app.after_request
def set_security_headers(response):
    """Add security headers to suppress browser warnings"""
    # Permissions-Policy: disable sensor access and other privacy concerns
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
    response.headers['Content-Security-Policy'] = (
        "default-src 'self' https:; "
        "script-src 'self' 'unsafe-inline' https://checkout.razorpay.com https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
        "img-src 'self' https: data:; "
        "connect-src 'self' https://api.razorpay.com https://*.razorpay.com; "
    )
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
        receipt_id = f"AAVAA-{serial}-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
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
            "Payment gateway unavailable. Please try again in a few minutes.",
            "error"
        )
        return redirect(url_for("ticket_page", serial=serial))

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
    ticket = get_or_create_ticket(serial)

    if not ticket["buyer_email"] or not ticket["category"]:
        return "Buyer details missing. Start again from the ticket page.", 400

    razorpay_payment_id = (
        request.args.get("razorpay_payment_id")
        or request.args.get("payment_id")
        or request.form.get("razorpay_payment_id")
    )
    razorpay_order_id = (
        request.args.get("razorpay_order_id")
        or request.form.get("razorpay_order_id")
    )
    razorpay_signature = (
        request.args.get("razorpay_signature")
        or request.form.get("razorpay_signature")
    )

    payment_verified = False

    if razorpay_payment_id:
        if not verify_razorpay_signature(
            razorpay_order_id, razorpay_payment_id, razorpay_signature
        ):
            return "Payment signature verification failed", 400

        try:
            payment = razorpay_client.payment.fetch(razorpay_payment_id)
            if payment.get("status") != "captured":
                return (
                    "Payment not completed successfully. "
                    "Please try again."
                ), 400
            payment_verified = True
        except Exception as e:
            app.logger.exception("Razorpay payment fetch failed")
            return f"Payment verification failed: {e}", 400

    if request.method == "GET" and not payment_verified:
        cat = CATEGORIES.get(ticket["category"], {})
        query_price = ticket.get(
            "total_price",
            cat.get("price", 0) * ticket.get("quantity", 1)
        )
        return safe_render_template(
            "confirm.html",
            serial=serial,
            category_name=cat.get("name", "Unknown"),
            price=query_price
        )

    # POST request OR auto-process verified Razorpay payment
    if request.method == "POST" or payment_verified:
        # Actually process the payment and send ticket
        category = ticket["category"]

        if category not in CATEGORIES:
            return "Invalid category", 400

        ticket["paid"] = True

        # 1. Save buyer details to Supabase first
        try:
            upsert_ticket_to_supabase(
                serial=serial,
                category=category,
                buyer_email=ticket["buyer_email"],
                quantity=ticket["quantity"],
                paid=True,
                ticket_image_url="",
                email_status="pending",
            )
        except Exception as e:
            return f"Supabase initial save failed: {e}", 500

        # 2. Generate ticket image
        try:
            image_path = generate_ticket_image_file(serial, category)
        except Exception as e:
            return f"Ticket image generation failed: {e}", 500

        image_public_url = (
            f"{PUBLIC_BASE_URL}/static/tickets/{image_path.name}"
        )
        ticket["ticket_image_url"] = image_public_url

        # 3. Update ticket image URL in Supabase
        try:
            upsert_ticket_to_supabase(
                serial=serial,
                category=category,
                buyer_email=ticket["buyer_email"],
                quantity=ticket["quantity"],
                paid=True,
                ticket_image_url=image_public_url,
                email_status="pending",
            )
        except Exception as e:
            return f"Supabase image URL update failed: {e}", 500

        # 4. Send email
        email_status = "pending"
        try:
            send_email_with_attachment(
                serial, ticket["buyer_email"], image_path
            )
            email_status = "sent"
        except Exception as e:
            print("Email error:", e)
            email_status = f"failed: {e}"

        # 5. Final update in Supabase
        try:
            upsert_ticket_to_supabase(
                serial=serial,
                category=category,
                buyer_email=ticket["buyer_email"],
                quantity=ticket["quantity"],
                paid=True,
                ticket_image_url=image_public_url,
                email_status=email_status,
            )
        except Exception as e:
            return f"Supabase final update failed: {e}", 500

        # final template render
        template_name = "ticketconfirmation.html"
        if not (BASE_DIR / "templates" / template_name).exists():
            template_name = "ticketconfimation.html"  # fallback typo

        final_price = ticket.get(
            "total_price",
            CATEGORIES[category]["price"] * ticket.get("quantity", 1),
        )

        return safe_render_template(
            template_name,
            serial=serial,
            category_name=cat["name"],
            category_key=category,
            price=final_price,
        )


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
            return styles_path.read_text(encoding="utf-8"), 200, {'Content-Type': 'text/css'}
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


if __name__ == "__main__":
    app.run(port=5002)
