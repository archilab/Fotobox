"""Create a non-person geometric test image; NOT a substitute for portrait realism."""
from pathlib import Path
from PIL import Image, ImageDraw
p=Path('storage/seed_portraits');p.mkdir(parents=True,exist_ok=True)
im=Image.new('RGB',(600,800),'#dddddd');d=ImageDraw.Draw(im)
d.ellipse((140,90,460,410),fill='#999999');d.rectangle((90,410,510,750),fill='#555555')
d.text((80,35),'SYNTHETIC SEED - NOT A PERSON',fill='black')
im.save(p/'synthetic-seed.png')
print(p/'synthetic-seed.png')
