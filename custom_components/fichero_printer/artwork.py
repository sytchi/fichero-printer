"""Turn generated artwork into something a one-bit thermal head can print.

The printer has no greys at all, and the picture ends up about eighty dots
tall, so anything soft or thin is lost. The prompts below ask for a technique
that is binary to begin with, and the conversion downscales first and
thresholds afterwards, which keeps thin strokes alive instead of shredding
them.
"""

from io import BytesIO

from PIL import Image, ImageOps

DEFAULT_THRESHOLD = 128

# A pictogram standing next to the label text.
ICON_PROMPT = (
    "Bold black-ink pictogram of {subject}, a single isolated object drawn like a hand-carved "
    "linocut rubber stamp. Solid pure black ink on a plain pure white background, strictly two "
    "colors, completely flat. Very thick uniform outlines, every line at least 1/25 of the image "
    "width thick, plus a few large solid black fills; gaps between lines as wide as the lines. "
    "Chunky simplified shapes with a strong, instantly recognizable silhouette that survives "
    "shrinking to a thumbnail; richer than a minimalist app icon but with no small details, "
    "no textures, no thin accents. Centered, front or three-quarter view, filling about 85% of "
    "the square canvas with an even white margin, nothing cut off. "
    "No text, letters, numbers, captions, watermark or signature. No frame, border, badge, "
    "circle or tile behind the object. No background scene, surface or props. "
    "No gray, gradients, shadows, highlights, hatching, stippling, halftone or dithering. "
    "Exactly one drawing, not a sheet of variants."
)

# A picture that fills the whole label, printed without any text.
ARTWORK_PROMPT = (
    "Wide horizontal banner illustration of {description}, drawn like a hand-carved linocut "
    "rubber stamp. Solid pure black ink on a plain pure white background, strictly two colors, "
    "completely flat. Very thick uniform outlines, every line at least 1/20 of the image height "
    "thick, combined with large solid black fills; gaps between lines as wide as the lines. "
    "Chunky simplified shapes and strong silhouettes, no small details, no textures, no thin "
    "accents. Panoramic composition: elements arranged side by side in one horizontal row that "
    "spans the full width from the left edge to the right edge. The image will be cropped to a "
    "strip about {aspect} times wider than it is tall, taken through the vertical center, so keep "
    "every important element inside that central band and leave plain empty white above and "
    "below it. If the description implies a "
    "logo or badge, draw it as a wordless emblem. "
    "No text, letters, numbers, lettering, watermark or signature. No frame, border, ornament "
    "or background scene. No gray, gradients, shadows, highlights, hatching, stippling, "
    "halftone or dithering."
)

# The label text is a menu entry, not a description of an object, so it is
# boiled down to one drawable noun before it reaches the image model. Polish
# left in the image prompt tends to come back written on the picture.
SUBJECT_INSTRUCTIONS = (
    "Turn this Polish label for a household container into a subject for a pictogram. "
    "Answer in English with 2 to 8 words naming one concrete object, no adjectives about "
    "taste, no sentence. A dish becomes the dish in its usual vessel; a substance or "
    "supplement becomes its packaging. Label: "
)


def _ink_box(image: Image.Image, threshold: int) -> tuple[int, int, int, int] | None:
    """Bounding box of everything darker than the threshold."""
    mask = image.point(lambda pixel: 255 if pixel < threshold else 0, mode="L")
    return mask.getbbox()


def to_printable(
    data: bytes,
    size: tuple[int, int],
    threshold: int = DEFAULT_THRESHOLD,
    crop_to_band: bool = False,
) -> Image.Image:
    """Fit generated artwork into `size` as pure black and white.

    `crop_to_band` takes the middle of the picture at the target aspect ratio
    first, which is how the wide label strip is cut out of a squarer image.
    """
    if not 1 <= threshold <= 254:
        raise ValueError("Threshold must be between 1 and 254")
    width, height = size
    if width <= 0 or height <= 0:
        raise ValueError("Artwork size must be positive")

    image = Image.open(BytesIO(data)).convert("L")
    if crop_to_band:
        band_height = min(image.height, max(1, round(image.width * height / width)))
        top = (image.height - band_height) // 2
        image = image.crop((0, top, image.width, top + band_height))

    box = _ink_box(image, threshold)
    if box:
        image = image.crop(box)

    # Downscale by averaging first: thresholding at full resolution and then
    # resampling loses exactly the thin strokes this artwork is made of.
    fitted = ImageOps.contain(image, size, Image.LANCZOS)
    canvas = Image.new("L", size, 255)
    canvas.paste(fitted, ((width - fitted.width) // 2, (height - fitted.height) // 2))
    return canvas.point(lambda pixel: 255 if pixel > threshold else 0, mode="1")


def to_png(image: Image.Image) -> bytes:
    """Serialise a converted picture so it can travel as base64."""
    buffer = BytesIO()
    image.convert("L").save(buffer, format="PNG")
    return buffer.getvalue()


def ink_ratio(image: Image.Image) -> float:
    """Share of black pixels, used to spot an empty or flooded conversion."""
    histogram = image.convert("L").histogram()
    black = histogram[0]
    return black / (image.width * image.height)
