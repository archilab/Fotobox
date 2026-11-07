"""Import a non-person geometric test image as a seed; NOT a substitute for portrait realism."""
import io
from PIL import Image, ImageDraw
from photobox_server.config import load_env_file
from photobox_server.core import db_connect
from photobox_server.pool import import_seed

load_env_file()
image = Image.new("RGB", (600, 800), "#dddddd")
draw = ImageDraw.Draw(image)
draw.ellipse((140, 90, 460, 410), fill="#999999")
draw.rectangle((90, 410, 510, 750), fill="#555555")
draw.text((80, 35), "SYNTHETIC SEED - NOT A PERSON", fill="black")
buffer = io.BytesIO()
image.save(buffer, "PNG")
with db_connect() as db:
    print(f"Seed imported: {import_seed(db, buffer.getvalue(), 'SYNTHETIC')}")
