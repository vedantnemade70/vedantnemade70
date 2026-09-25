# Vedant Nemade — Portfolio

Personal portfolio website for **Vedant Milind Nemade**, Civil Engineering student and road-construction Site Supervisor (Pooja Construction, Bhusawal).

![Portfolio preview](assets/preview.png)

A static site (HTML, CSS and a little JavaScript) with no build step.

## Run locally
Open `index.html` in a browser.

## Publish with GitHub Pages
1. Repo **Settings → Pages**
2. Source: **Deploy from a branch** → pick the branch and `/ (root)` → Save
3. The site goes live at `https://vedantnemade70.github.io/<repo-name>/`

## Structure
```
index.html        page content
style.css         styles (asphalt + safety-orange theme)
script.js         mobile menu, project filters, counters, scroll effects
assets/           hero photo and downloadable resume
```

## Editing
- Projects: edit the `<article class="project">` blocks in `index.html`. `data-type` controls which filter button shows the project (`asphalt`, `concrete`, `highway`).
- Colors: change `--orange` and `--asphalt` at the top of `style.css`.
- Resume: replace `assets/Vedant_Nemade_Resume.docx`. A PDF works in more browsers, so if you switch to one, update the two download links.
