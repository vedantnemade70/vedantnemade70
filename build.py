"""Build portfolio.html: a single self-contained copy of the site.

index.html needs style.css, script.js and assets/ next to it. portfolio.html
has all of them embedded, so it can be emailed or opened on its own.
Run `python3 build.py` after editing any of the source files.
"""
import base64
import pathlib

root = pathlib.Path(__file__).parent


def data_uri(path, mime):
    return f"data:{mime};base64," + base64.b64encode((root / path).read_bytes()).decode()


html = (root / "index.html").read_text()
css = (root / "style.css").read_text()
js = (root / "script.js").read_text()

hero = data_uri("assets/hero-asphalt.jpg", "image/jpeg")
resume = data_uri(
    "assets/Vedant_Nemade_Resume.docx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
)

# Define the photo once as a CSS variable instead of repeating it in every rule.
css = css.replace('url("assets/hero-asphalt.jpg")', "var(--hero-img)")
css = f':root {{ --hero-img: url("{hero}"); }}\n' + css

html = html.replace('<link rel="stylesheet" href="style.css" />', f"<style>\n{css}</style>")
html = html.replace('<script src="script.js"></script>', f"<script>\n{js}</script>")
html = html.replace('src="assets/hero-asphalt.jpg"', f'src="{hero}"')
html = html.replace(
    'href="assets/Vedant_Nemade_Resume.docx" ',
    f'href="{resume}" download="Vedant_Nemade_Resume.docx" ',
)
html = html.replace(" download>", ">")  # drop the now-duplicate bare attribute

assert "assets/" not in html and "style.css" not in html and "script.js" not in html
(root / "portfolio.html").write_text(html)
print(f"portfolio.html written ({len(html) // 1024} KB)")
