#!/usr/bin/env python3
"""
Generate professional AAVADINGI ticket images with proper styling
"""

from PIL import Image, ImageDraw, ImageFont, ImageFilter
import os
from pathlib import Path

# Create static/images directory
STATIC_DIR = Path(__file__).parent / "static" / "images"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

# Font configuration
FONT_PATHS = [
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\ARIALUNI.TTF",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

def get_font(size):
    """Get available font or use default"""
    for path in FONT_PATHS:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except:
                pass
    return ImageFont.load_default()

def create_ticket(filename, tier_name, price, color_bg, color_accent, color_text):
    """Create individual ticket image"""
    width, height = 1000, 500
    
    # Create base image with gradient
    img = Image.new('RGB', (width, height), color_bg)
    draw = ImageDraw.Draw(img)
    
    # Draw decorative border
    border_width = 5
    draw.rectangle(
        [(border_width, border_width), 
         (width - border_width, height - border_width)],
        outline=color_accent,
        width=border_width
    )
    
    # Draw inner decorative border
    draw.rectangle(
        [(border_width + 10, border_width + 10), 
         (width - border_width - 10, height - border_width - 10)],
        outline=color_accent,
        width=2
    )
    
    # Title - Tier Name
    title_font = get_font(80)
    draw.text(
        (width // 2, height // 2 - 80),
        tier_name,
        fill=color_text,
        font=title_font,
        anchor="mm"
    )
    
    # Price
    price_font = get_font(70)
    draw.text(
        (width // 2, height // 2 + 40),
        price,
        fill=color_accent,
        font=price_font,
        anchor="mm"
    )
    
    # Event name at bottom
    event_font = get_font(24)
    draw.text(
        (width // 2, height - 50),
        "AAVADINGI 2026 | Chennai | 23 & 24 May",
        fill=color_text,
        font=event_font,
        anchor="mm"
    )
    
    # Save image
    img.save(STATIC_DIR / filename, "JPEG", quality=95)
    print(f"✓ {filename} created")

# Create all ticket images with distinct colors
tickets = [
    ("general.jpeg", "GENERAL", "₹500", (255, 100, 100), (255, 200, 0), (255, 255, 255)),
    ("silver.jpeg", "SILVER", "₹1200", (192, 192, 192), (220, 220, 220), (50, 50, 50)),
    ("gold.jpeg", "GOLD", "₹1999", (184, 134, 11), (255, 215, 0), (255, 255, 255)),
    ("platinum.jpeg", "PLATINUM", "₹2999", (75, 0, 130), (0, 255, 255), (255, 255, 255)),
    ("vip.jpeg", "VIP", "₹4999", (139, 0, 0), (255, 215, 0), (255, 255, 255)),
]

print("Creating ticket images...\n")
for filename, name, price, bg, accent, text in tickets:
    create_ticket(filename, name, price, bg, accent, text)

print("\n✅ All ticket images created successfully!")
print(f"Saved to: {STATIC_DIR}")
