from pathlib import Path
from PIL import Image, ImageDraw

size = 1024
image = Image.new("RGBA", (size, size))
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((24, 24, 1000, 1000), radius=215, fill="#236b45")
draw.rounded_rectangle((254, 168, 772, 830), radius=42, fill="#f7f9f3")
draw.rounded_rectangle((322, 245, 492, 299), radius=15, fill="#b4cba3")
for y, end in ((383, 695), (473, 695), (563, 535), (729, 454)):
    draw.rounded_rectangle((322, y, end, y + 23), radius=10, fill="#d4deca")
draw.ellipse((582, 581, 880, 879), fill="#84aa66")
draw.line([(650, 733), (710, 793), (818, 669)], fill="#ffffff", width=29, joint="curve")
Path("build").mkdir(exist_ok=True)
image.save("build/icon.png")
